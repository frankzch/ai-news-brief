import os
import json
import logging
import random
import shutil
import subprocess
import tempfile
import threading
import time

KEYCHAIN_SERVICE = "inbrief-claude-oauth"

CLI_RETRY_DELAYS = (5, 20)   # seconds before retry 1 and 2 after a crash/timeout (jittered +/-25%)
CLI_RETRY_JITTER = 0.25
CLI_BREAKER_THRESHOLD = 3    # consecutive crashes/timeouts before a cooldown
CLI_BREAKER_COOLDOWN = 60    # seconds


class SummarizationError(Exception):
    """The LLM call behind a summary failed for good (after retries); the item was not summarized."""


def _read_oauth_token():
    """Claude Code OAuth token: env var first, then macOS Keychain (never a dotfile)."""
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if token:
        return token.strip()
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _extract_json(text):
    """Parse a JSON object from model output, tolerating code fences or stray prose."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        candidate = text[start:end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # Typically unescaped quotes inside long string values; repair rather than burn another LLM call.
            from json_repair import repair_json
            repaired = repair_json(candidate, return_objects=True)
            if isinstance(repaired, dict) and repaired:
                return repaired
            raise


class AIEngine:
    
    def __init__(self, config):
        self.backend = config.get('backend', 'openai')
        self.model = config['model_name']
        if self.backend == 'claude_cli':
            self.claude_bin = config.get('claude_cli_path') or shutil.which('claude')
            if not self.claude_bin:
                raise RuntimeError("llm.backend=claude_cli but the `claude` CLI was not found "
                                   "(set llm.claude_cli_path or add it to PATH)")
            self.model = config.get('claude_cli_model', 'haiku')
            self.oauth_token = _read_oauth_token()
            if not self.oauth_token:
                raise RuntimeError(f"No Claude token: run `claude setup-token`, then store it with "
                                   f"`security add-generic-password -s {KEYCHAIN_SERVICE} -a \"$USER\" -w`")
            self.client = None
        else:
            from openai import OpenAI
            self.client = OpenAI(
                api_key=os.environ.get("DEEPSEEK_API_KEY") or config['api_key'],
                base_url=config['base_url']
            )
        self._sleep = time.sleep  # injectable so tests don't wait
        self._cli_sem = threading.BoundedSemaphore(max(1, int(config.get('claude_cli_max_concurrency', 3))))
        self._breaker_lock = threading.Lock()
        self._consecutive_cli_failures = 0
        # Load AI timeout from fetching config
        from config_loader import ConfigLoader
        fetching_config = ConfigLoader.get_instance().get('fetching', {})
        self.ai_timeout = fetching_config.get('ai_timeout', 90)
        self.max_input_chars = fetching_config.get('max_input_chars', 8000)
        # 提示词目录路径
        self.prompts_dir = os.path.join(os.path.dirname(__file__), 'prompts')
        # 预加载通用提示词
        self._prompt_cache = {}

    def _complete_json(self, system_prompt, user_content, temperature, timeout=None):
        """Run one JSON-returning completion on the configured backend and return the parsed dict."""
        timeout = timeout if timeout else self.ai_timeout
        if self.backend == 'claude_cli':
            return _extract_json(self._claude_cli(system_prompt, user_content, timeout))
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_content})
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            response_format={"type": "json_object"},
            timeout=timeout,
        )
        return json.loads(response.choices[0].message.content.strip())

    def _claude_cli(self, system_prompt, user_content, timeout):
        """One-shot `claude -p` call: no tools, no session, subscription auth via OAuth token."""
        cmd = [self.claude_bin, "-p", "--model", self.model, "--tools", "",
               "--no-session-persistence", "--output-format", "text"]
        if system_prompt:
            cmd += ["--system-prompt", system_prompt]
        env = {**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": self.oauth_token}
        env.pop("ANTHROPIC_API_KEY", None)  # make sure the subscription token is what gets used
        payload = user_content + "\n\nRespond with a single JSON object only; escape any double quotes inside string values."
        # Signal crashes (negative return code, e.g. -11 = SIGSEGV) and timeouts are usually transient
        # (machine under pressure), so retry them after a jittered backoff. Ordinary CLI errors are not retried.
        for attempt in range(len(CLI_RETRY_DELAYS) + 1):
            self._breaker_wait()
            try:
                with self._cli_sem, tempfile.TemporaryDirectory() as cwd:  # empty cwd: no CLAUDE.md / project settings picked up
                    proc = subprocess.run(cmd, input=payload, capture_output=True, text=True,
                                          timeout=timeout, env=env, cwd=cwd)
            except subprocess.TimeoutExpired:
                reason = f"timed out after {timeout}s"
                error = RuntimeError(f"claude CLI {reason}")
            else:
                if proc.returncode == 0:
                    self._breaker_record(ok=True)
                    return proc.stdout
                error = RuntimeError(f"claude CLI exited {proc.returncode}: {proc.stderr.strip()[:300]}")
                if proc.returncode > 0:
                    raise error
                reason = f"killed by signal {-proc.returncode}"
            self._breaker_record(ok=False)
            if attempt == len(CLI_RETRY_DELAYS):
                raise error
            delay = CLI_RETRY_DELAYS[attempt] * random.uniform(1 - CLI_RETRY_JITTER, 1 + CLI_RETRY_JITTER)
            logging.warning(f"claude CLI {reason}, retry {attempt + 1}/{len(CLI_RETRY_DELAYS)} in {delay:.1f}s")
            self._sleep(delay)

    def _breaker_record(self, ok):
        with self._breaker_lock:
            self._consecutive_cli_failures = 0 if ok else self._consecutive_cli_failures + 1

    def _breaker_wait(self):
        """After a burst of consecutive crashes/timeouts, pause once (blocking other callers) before trying again."""
        with self._breaker_lock:
            if self._consecutive_cli_failures >= CLI_BREAKER_THRESHOLD:
                logging.warning(f"claude CLI failed {self._consecutive_cli_failures} times in a row, "
                                f"pausing {CLI_BREAKER_COOLDOWN}s")
                self._consecutive_cli_failures = 0
                self._sleep(CLI_BREAKER_COOLDOWN)

    def _get_prompt_for_category(self, category: str) -> str:
        """
        根据文章类别读取对应的提示词文件。
        
        Args:
            category: 文章类别名称
            
        Returns:
            提示词内容字符串
        """
        if not category:
            category = 'default'
            
        # 获取对应的提示词文件名
        prompt_file = category.lower() + '.txt'
        prompt_path = os.path.join(self.prompts_dir, prompt_file)
        
        try:
            with open(prompt_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            logging.warning(f"Prompt file not found: {prompt_path}, using default")
            # 如果找不到，尝试读取默认提示词
            default_path = os.path.join(self.prompts_dir, 'default.txt')
            try:
                with open(default_path, 'r', encoding='utf-8') as f:
                    return f.read()
            except FileNotFoundError:
                logging.error("Default prompt file not found")
                return ""

    def _load_prompt(self, filename: str) -> str:
        """加载指定的提示词文件"""
        if filename in self._prompt_cache:
            return self._prompt_cache[filename]
        
        prompt_path = os.path.join(self.prompts_dir, filename)
        try:
            with open(prompt_path, 'r', encoding='utf-8') as f:
                content = f.read()
                self._prompt_cache[filename] = content
                return content
        except FileNotFoundError:
            logging.error(f"Prompt file not found: {prompt_path}")
            return ""

    def summarize_content(self, text, title=None, category=None, timeout=None):
        """
        Summarizes the article content with bilingual (English + Chinese) summaries and titles.
        
        Args:
            text: 文章内容
            title: 原标题（可选）
            category: 文章类别（可选），用于选择对应的提示词
            timeout: 超时时间
            
        Returns a tuple: (summary_en, summary_zh, long_summary_en, long_summary_zh, importance_score, tags, title_en, title_zh)
        """
        title_section = f'Original Title: "{title}"\n\n' if title else ''
        
        # 根据类别获取对应的提示词，作为 system 消息
        system_prompt = self._get_prompt_for_category(category)
        
        # 文章内容作为 user 消息，末尾追加格式提醒以利用结尾高注意力
        user_content = f"""{title_section}Text:
{text[:self.max_input_chars]}

---
Reminder: You MUST output valid JSON in the exact format specified in your instructions. 严格遵守所有约束条件。"""

        # LLM 偶发漏填某一字段，导致前端空摘要。校验摘要完整性，缺失则重试一次：
        # 双语短摘要必须都有；长摘要仅在原文够长（≥1000 字，与提示词阈值一致）时才要求，
        # 原文过短允许省略长摘要。china_blocked 或 score=0 的文章下游本就会丢弃，无需重试。
        long_required = len(text or '') >= 1000
        last_data = None
        last_error = None
        for attempt in range(2):
            try:
                data = self._complete_json(system_prompt, user_content, 0.3, timeout)
            except Exception as e:
                logging.error(f"AI Summarization failed (attempt {attempt + 1}/2): {e}")
                last_error = e
                # claude CLI crashes/timeouts were already retried with backoff in _claude_cli; re-asking
                # at once would only hit the same fault. Malformed JSON output is worth one more ask.
                if self.backend == 'claude_cli' and not isinstance(e, ValueError):
                    break
                continue

            last_data = data
            will_be_dropped = data.get('china_blocked', False) or data.get('importance_score', 0) == 0
            short_ok = bool(data.get('summary_en', '').strip()) and bool(data.get('summary_zh', '').strip())
            long_ok = (not long_required) or (
                bool(data.get('long_summary_en', '').strip()) and bool(data.get('long_summary_zh', '').strip())
            )
            if will_be_dropped or (short_ok and long_ok):
                break
            logging.warning(
                f"Incomplete summary (short_ok={short_ok}, long_ok={long_ok}, long_required={long_required}), "
                f"retrying... title={title[:40] if title else ''}"
            )

        if last_data is None:
            raise SummarizationError(str(last_error)) from last_error

        tags = last_data.get('tags', [])
        if len(tags) > 5:
            tags = tags[:5]
        return (
            last_data.get('summary_en', ''),
            last_data.get('summary_zh', ''),
            last_data.get('long_summary_en', ''),
            last_data.get('long_summary_zh', ''),
            last_data.get('importance_score', 0),
            tags,
            last_data.get('title_en', ''),
            last_data.get('title_zh', ''),
            last_data.get('china_blocked', False)
        )



    def analyze_user_persona(self, interest_description: str) -> dict:
        """
        Analyze user persona based on their interest description.
        Returns dict with 'positive_interest', 'negative_tags', and 'categories'.
        """
        prompt_template = self._load_prompt('analyze_user_persona.txt')
        prompt = prompt_template.format(
            interest_description=interest_description if interest_description else "用户未提供自述"
        )

        try:
            data = self._complete_json(None, prompt, 0.3)
            
            # Ensure lists are valid
            categories = data.get('categories', [])
            if not isinstance(categories, list):
                categories = []
            
            negative_tags = data.get('negative_tags', [])
            if not isinstance(negative_tags, list):
                negative_tags = []
            
            return {
                'positive_interest': data.get('positive_interest', ''),
                'negative_tags': negative_tags[:5],  # Max 5 negative tags
                'categories': categories[:10],  # Max 10 categories
                # Backward compatibility
                'persona': data.get('positive_interest', '')
            }
        except Exception as e:
            logging.error(f"AI Persona Analysis failed: {e}")
            return None

    def recommend_rss_sources(self, user_persona: str, interest_description: str,
                               existing_sources: list = None) -> list:
        """
        Recommend RSS sources based on user persona and interests.
        
        Args:
            user_persona: AI-generated user persona description
            interest_description: User's self-described interests
            
        Returns:
            List of dicts with 'url', 'name', 'description' for recommended RSS feeds
        """
        
        prompt_template = self._load_prompt('recommend_rss_sources.txt')
        prompt = prompt_template.format(
            user_persona=user_persona if user_persona else "暂无用户画像",
            interest_description=interest_description if interest_description else "用户未提供兴趣描述"
        )

        try:
            data = self._complete_json(None, prompt, 0.4)
            
            sources = data.get('rss_sources', [])
            
            # Validate and clean up results
            valid_sources = []
            for src in sources:
                if src.get('url') and src.get('name'):
                    valid_sources.append({
                        'url': src['url'].strip(),
                        'name': src['name'].strip(),
                        'description': src.get('description', '').strip()
                    })
            
            logging.info(f"AI recommended {len(valid_sources)} RSS sources")
            return valid_sources
            
        except Exception as e:
            logging.error(f"AI RSS Recommendation failed: {e}")
            return []


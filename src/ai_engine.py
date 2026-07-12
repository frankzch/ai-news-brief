import os
from openai import OpenAI
import json
import logging

class AIEngine:
    
    def __init__(self, config):
        self.client = OpenAI(
            api_key=os.environ.get("DEEPSEEK_API_KEY") or config['api_key'],
            base_url=config['base_url']
        )
        self.model = config['model_name']
        # Load AI timeout from fetching config
        from config_loader import ConfigLoader
        fetching_config = ConfigLoader.get_instance().get('fetching', {})
        self.ai_timeout = fetching_config.get('ai_timeout', 90)
        self.max_input_chars = fetching_config.get('max_input_chars', 8000)
        # 提示词目录路径
        self.prompts_dir = os.path.join(os.path.dirname(__file__), 'prompts')
        # 预加载通用提示词
        self._prompt_cache = {}

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
        for attempt in range(2):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content}
                    ],
                    temperature=0.3,
                    response_format={"type": "json_object"},
                    timeout=timeout if timeout else self.ai_timeout
                )
                data = json.loads(response.choices[0].message.content.strip())
            except Exception as e:
                logging.error(f"AI Summarization failed (attempt {attempt + 1}/2): {e}")
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
            return '', '', '', '', 0, [], '', '', False

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
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            content = response.choices[0].message.content.strip()
            data = json.loads(content)
            
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
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                response_format={"type": "json_object"}
            )
            content = response.choices[0].message.content.strip()
            data = json.loads(content)
            
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


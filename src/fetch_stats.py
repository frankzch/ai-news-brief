"""
FetchStats - 按 RSS URL 分类统计抓取信息

用于收集和汇总 RSS 抓取过程中的统计数据，包括：
- 成功抓取数
- 跳过数及原因
- 失败数及原因  
- LLM 输入/输出字数
"""

import logging
import threading
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class SourceStats:
    """单个 RSS 源的统计数据"""
    description: str = ""
    fetched_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    skip_reasons: Dict[str, int] = field(default_factory=dict)
    fail_reasons: Dict[str, int] = field(default_factory=dict)
    llm_input_chars: int = 0
    llm_output_chars: int = 0


class FetchStats:
    """按 RSS URL 分类统计抓取信息（线程安全）"""
    
    def __init__(self):
        self.sources: Dict[str, SourceStats] = {}
        self._lock = threading.Lock()
    
    def _get_or_create(self, rss_url: str, rss_description: str = "") -> SourceStats:
        """获取或创建 RSS 源的统计对象（调用方需持有锁）"""
        if rss_url not in self.sources:
            self.sources[rss_url] = SourceStats(description=rss_description)
        elif rss_description and not self.sources[rss_url].description:
            self.sources[rss_url].description = rss_description
        return self.sources[rss_url]
    
    def record_success(self, rss_url: str, rss_description: str, 
                       llm_input_chars: int, llm_output_chars: int):
        """记录成功抓取"""
        with self._lock:
            stats = self._get_or_create(rss_url, rss_description)
            stats.fetched_count += 1
            stats.llm_input_chars += llm_input_chars
            stats.llm_output_chars += llm_output_chars
    
    def record_skip(self, rss_url: str, rss_description: str, reason: str):
        """记录跳过"""
        with self._lock:
            stats = self._get_or_create(rss_url, rss_description)
            stats.skipped_count += 1
            stats.skip_reasons[reason] = stats.skip_reasons.get(reason, 0) + 1
    
    def record_failure(self, rss_url: str, rss_description: str, reason: str):
        """记录失败"""
        with self._lock:
            stats = self._get_or_create(rss_url, rss_description)
            stats.failed_count += 1
            stats.fail_reasons[reason] = stats.fail_reasons.get(reason, 0) + 1
    
    def _format_reasons(self, reasons: Dict[str, int]) -> str:
        """格式化原因统计"""
        if not reasons:
            return ""
        parts = [f"{k}: {v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])]
        return f" ({', '.join(parts)})"
    
    def _format_number(self, n: int) -> str:
        """格式化数字，添加千位分隔符"""
        return f"{n:,}"
    
    def get_summary(self) -> str:
        """返回格式化的汇总报告"""
        if not self.sources:
            return "No fetch statistics recorded."
        
        lines = []
        lines.append("")
        lines.append("=" * 60)
        lines.append("               FETCH STATISTICS REPORT")
        lines.append("=" * 60)
        
        total_fetched = 0
        total_skipped = 0
        total_failed = 0
        total_llm_input = 0
        total_llm_output = 0
        
        for rss_url, stats in self.sources.items():
            # 使用 description 作为标题，如果没有则使用 URL
            title = stats.description or rss_url
            lines.append("")
            lines.append(f"📊 {title}")
            lines.append(f"   ├─ Fetched: {stats.fetched_count} articles")
            
            if stats.skipped_count > 0:
                skip_detail = self._format_reasons(stats.skip_reasons)
                lines.append(f"   ├─ Skipped: {stats.skipped_count}{skip_detail}")
            
            if stats.failed_count > 0:
                fail_detail = self._format_reasons(stats.fail_reasons)
                lines.append(f"   ├─ Failed:  {stats.failed_count}{fail_detail}")
            
            if stats.llm_input_chars > 0 or stats.llm_output_chars > 0:
                lines.append(f"   └─ LLM Chars: Input {self._format_number(stats.llm_input_chars)} | Output {self._format_number(stats.llm_output_chars)}")
            else:
                # 确保最后一行使用 └─
                if lines[-1].startswith("   ├─"):
                    lines[-1] = lines[-1].replace("├─", "└─", 1)
            
            total_fetched += stats.fetched_count
            total_skipped += stats.skipped_count
            total_failed += stats.failed_count
            total_llm_input += stats.llm_input_chars
            total_llm_output += stats.llm_output_chars
        
        lines.append("")
        lines.append("=" * 60)
        lines.append(f"TOTAL: Fetched {total_fetched} | Skipped {total_skipped} | Failed {total_failed}")
        lines.append(f"LLM Chars: Input {self._format_number(total_llm_input)} | Output {self._format_number(total_llm_output)}")
        lines.append("=" * 60)
        
        return "\n".join(lines)
    
    def log_report(self):
        """输出统计报告到日志"""
        report = self.get_summary()
        for line in report.split("\n"):
            if line.strip():
                logging.info(line)

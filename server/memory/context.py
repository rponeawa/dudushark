"""
上下文窗口管理 — 128K token 最大，超出时压缩旧消息。
使用简单字符估算（中文约 1 字符 ≈ 1.5 token，英文约 4 字符 ≈ 1 token）。

压缩策略：
1. 优先保留最新消息（从末尾向前填充）。
2. 被截断的消息压缩为摘要，插入到 system message 之后。
3. 如果已有旧摘要，自动合并以避免摘要堆积。
4. 摘要本身也计入 token 预算，确保不超过限制。
"""

SUMMARY_PREFIX = "（以下为更早对话的摘要："


class ContextManager:
    def __init__(self, max_tokens: int = 128000, reserve_for_reply: int = 4000):
        self.max_tokens = max_tokens
        self.reserve = reserve_for_reply

    def count_tokens(self, text: str) -> int:
        if not text:
            return 0
        chinese = sum(1 for c in text if "一" <= c <= "鿿")
        other = len(text) - chinese
        return int(chinese * 1.5 + other / 3.5) + 1

    def count_messages(self, messages: list[dict]) -> int:
        return sum(self.count_tokens(m.get("content", "")) + 4 for m in messages) + 2

    def fit_messages(
        self, system_prompt: str, messages: list[dict]
    ) -> list[dict]:
        """
        返回压缩后的消息列表，保证不超过 token 限制。
        包含 system prompt + 旧摘要 + 最近消息。
        记忆和 mood 由调用者作为独立 system 消息添加（提高缓存命中率）。
        """
        budget = self.max_tokens - self.reserve - 20

        # ---- 整理旧摘要 ----
        normal_msgs = []
        old_summaries: list[str] = []
        for m in messages:
            content = m.get("content", "")
            if content.startswith(SUMMARY_PREFIX):
                old_summaries.append(content)
            else:
                normal_msgs.append(m)

        coalesced_summary = self._coalesce_summaries(old_summaries)

        # ---- 构建 system 部分（仅 prompt + 摘要） ----
        system_parts = [system_prompt]
        if coalesced_summary:
            system_parts.append(coalesced_summary)
        system_content = "\n\n".join(system_parts)
        system_tokens = self.count_tokens(system_content)
        budget -= system_tokens

        if budget <= 0:
            # system prompt 本身已经过长，截断
            truncated = system_prompt[: self.max_tokens // 3]
            return [{"role": "system", "content": truncated}]

        # ---- 从末尾向前填充消息 ----
        result = []
        remaining = budget
        kept_from_end = 0
        for m in reversed(normal_msgs):
            tok = self.count_tokens(m.get("content", "")) + 4
            if tok > remaining:
                break
            remaining -= tok
            result.insert(0, m)
            kept_from_end += 1

        # ---- 处理被截断的部分 ----
        cut_count = len(normal_msgs) - kept_from_end
        if cut_count > 0:
            cut = normal_msgs[:cut_count]
            # 将被截断的消息追加到旧摘要中
            new_summary_text = self._messages_to_summary(cut)
            if coalesced_summary:
                new_summary_text = self._coalesce_summaries([coalesced_summary, new_summary_text])
            summary_tokens = self.count_tokens(new_summary_text)
            # 需要为摘要腾出空间
            while result and summary_tokens > remaining:
                removed = result.pop(0)
                remaining += self.count_tokens(removed.get("content", "")) + 4
            if summary_tokens <= remaining:
                sys_content = system_prompt + f"\n\n{new_summary_text}"
                # 重建 system message
                result.insert(0, {"role": "system", "content": sys_content})

        if not result or result[0].get("role") != "system":
            result.insert(0, {"role": "system", "content": system_content})

        return result

    def _messages_to_summary(self, msgs: list[dict], max_per_msg: int = 80) -> str:
        """将消息列表压缩为单行摘要。"""
        if not msgs:
            return ""
        lines = []
        for m in msgs[-40:]:
            content = m.get("content", "")
            role = m.get("role", "?")
            label = "用户" if role == "user" else "助手" if role == "assistant" else role
            lines.append(f"[{label}]: {content[:max_per_msg]}")
        return SUMMARY_PREFIX + " | ".join(lines) + "）"

    def _coalesce_summaries(self, summaries: list[str]) -> str:
        """合并多个摘要，去重并限制总长度。"""
        if not summaries:
            return ""
        # 提取摘要内容（去掉前缀）
        contents = []
        for s in summaries:
            if s.startswith(SUMMARY_PREFIX) and s.endswith("）"):
                inner = s[len(SUMMARY_PREFIX):-1]
                contents.append(inner)
            else:
                contents.append(s)
        combined = " | ".join(contents)
        # 摘要不超过 3000 字符
        if len(combined) > 3000:
            combined = combined[:1500] + " | ... | " + combined[-1500:]
        return SUMMARY_PREFIX + combined + "）"

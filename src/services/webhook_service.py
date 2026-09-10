"""WeCom Webhook Notification Service for Flow2API"""
import asyncio
from datetime import datetime, timezone
import json
import logging
import re
from typing import Any, Dict, List, Optional
from curl_cffi.requests import AsyncSession

from src.core.database import Database
from src.core.logger import debug_logger


class WebhookService:
    _instance: Optional["WebhookService"] = None

    def __init__(self, db: Database):
        self.db = db
        self._scheduler_task: Optional[asyncio.Task] = None
        self._running = False
        self._last_daily_report_date: str = ""
        # 缓存已通知过失效的 token: token_id -> (timestamp, reason)
        self._notified_expired_tokens: Dict[int, tuple[float, str]] = {}

    @classmethod
    def get_instance(cls, db: Optional[Database] = None) -> "WebhookService":
        if cls._instance is None:
            if db is None:
                raise RuntimeError("WebhookService not initialized with database")
            cls._instance = cls(db)
        return cls._instance

    @staticmethod
    def _markdown_to_clean_text(md: str) -> str:
        """将 Markdown 转换为排版清晰的纯文本格式，方便在微信端直接阅读"""
        text = md
        # 移除 <font ...> 和 </font>
        text = re.sub(r'<font[^>]*>(.*?)</font>', r'\1', text)
        # 转换 ### 标题 为 【标题】
        text = re.sub(r'^###\s+(.*)$', r'【\1】', text, flags=re.MULTILINE)
        text = re.sub(r'^####\s+(.*)$', r'【\1】', text, flags=re.MULTILINE)
        text = re.sub(r'^##\s+(.*)$', r'【\1】', text, flags=re.MULTILINE)
        # 移除引用符 >
        text = re.sub(r'^>\s*', '', text, flags=re.MULTILINE)
        # 移除加粗 **
        text = text.replace('**', '')
        # 移除代码反引号 `
        text = text.replace('`', '')
        return text.strip()

    async def send_wecom_message(
        self,
        content_markdown: str,
        content_text: Optional[str] = None,
        custom_url: Optional[str] = None,
        custom_msg_type: Optional[str] = None
    ) -> tuple[bool, str]:
        """向企业微信群机器人 Webhook 发送消息（支持 markdown 或纯文本）"""
        cfg = await self.db.get_webhook_config()
        url = (custom_url or cfg.wecom_webhook_url or "").strip()

        if not url:
            return False, "未配置企业微信 Webhook URL"
        if not (url.startswith("https://qyapi.weixin.qq.com/") or url.startswith("http://") or url.startswith("https://")):
            return False, "企业微信 Webhook URL 格式不正确（应以 https://qyapi.weixin.qq.com/ 开头）"

        target_type = (custom_msg_type or cfg.msg_type or "markdown").strip().lower()

        if target_type == "text":
            text = content_text if content_text is not None else self._markdown_to_clean_text(content_markdown)
            # 企业微信 text 类型最大 2048 字节
            encoded = text.encode("utf-8")
            if len(encoded) > 2000:
                text = encoded[:1950].decode("utf-8", errors="ignore") + "\n\n...(内容过长已截断)"
            payload = {
                "msgtype": "text",
                "text": {
                    "content": text
                }
            }
        else:
            # 企业微信 markdown 类型最大 4096 字节
            encoded = content_markdown.encode("utf-8")
            if len(encoded) > 4000:
                content_markdown = encoded[:3950].decode("utf-8", errors="ignore") + "\n\n...(内容过长已截断)"
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "content": content_markdown
                }
            }

        try:
            async with AsyncSession(trust_env=False) as session:
                resp = await session.post(
                    url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=15
                )
                if resp.status_code == 200:
                    data = resp.json() or {}
                    if data.get("errcode") == 0:
                        return True, "发送成功"
                    return False, f"企业微信返回错误: {data.get('errmsg', '未知错误')} (errcode: {data.get('errcode')})"
                return False, f"HTTP 请求失败: HTTP {resp.status_code}"
        except Exception as e:
            debug_logger.log_error(f"[WEBHOOK] 发送企业微信通知异常: {e}")
            return False, f"发送网络异常: {e}"

    async def send_wecom_markdown(self, content: str, custom_url: Optional[str] = None) -> tuple[bool, str]:
        """向企业微信群机器人 Webhook 发送 Markdown 消息（兼容接口）"""
        return await self.send_wecom_message(content, custom_url=custom_url, custom_msg_type="markdown")

    async def send_test_message(self, custom_url: Optional[str] = None, custom_msg_type: Optional[str] = None) -> tuple[bool, str]:
        """发送测试消息"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        markdown = f"""### 🔔 Flow2API 企业微信通知测试

> **测试时间**：{now_str}
> **服务状态**：<font color="info">正常运行</font>

如果您收到了此条消息，说明 Flow2API 的企业微信 Webhook 配置正确，已可正常接收实时账号告警与每日统计汇报！"""

        text = f"""【🔔 Flow2API 企业微信通知测试】

测试时间：{now_str}
服务状态：正常运行

如果您收到了此条消息，说明 Flow2API 的企业微信 Webhook 配置正确，已可正常接收实时账号告警与每日统计汇报！"""

        return await self.send_wecom_message(markdown, content_text=text, custom_url=custom_url, custom_msg_type=custom_msg_type)

    def mark_token_recovered(self, token_id: int):
        """当账号恢复正常（重新导入/启用/刷新成功）时，清除失效通知标记"""
        if token_id in self._notified_expired_tokens:
            debug_logger.log_info(f"[WEBHOOK] Token {token_id} 已恢复正常，清除失效通知标记")
            self._notified_expired_tokens.pop(token_id, None)

    async def notify_token_expired(self, token_id: int, reason: str, token_obj: Optional[Any] = None) -> bool:
        """当 Token 失效/过期/禁用时，发送企业微信实时告警通知（每个账号失效期间只发一次，绝不重复轰炸）"""
        try:
            cfg = await self.db.get_webhook_config()
            if not cfg.enabled or not cfg.notify_on_expired or not cfg.wecom_webhook_url:
                return False

            token = token_obj or await self.db.get_token(token_id)
            if not token:
                return False

            is_active = bool(getattr(token, "is_active", False))
            at_expires = getattr(token, "at_expires", None)

            # 防误报过滤：如果账号依然处于启用状态且 AT 尚未过期，说明账号完全可用，清除记录并不发送告警
            if is_active and at_expires:
                now_utc = datetime.now(timezone.utc)
                at_exp = at_expires if at_expires.tzinfo else at_expires.replace(tzinfo=timezone.utc)
                if at_exp > now_utc:
                    self.mark_token_recovered(token_id)
                    return False

            # 单次告警限制：只要该账号已发送过失效告警且尚未恢复正常，坚决不再重复发送
            if token_id in self._notified_expired_tokens:
                return False

            import time
            now_ts = time.time()

            email = getattr(token, "email", None) or f"Token ID: {token_id}"
            credits = getattr(token, "credits", 0) or 0
            status_text = "已禁用" if not is_active else "已过期"

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            markdown = f"""### 🚨 Flow2API 账号失效告警

> **告警时间**：{now_str}
> **账号邮箱**：<font color="warning">{email}</font>
> **当前状态**：{status_text}
> **剩余积分**：{credits} 点
> **失效原因**：<font color="warning">{reason}</font>

💡 **处理建议**：请在对应电脑浏览器中打开 Flow（`https://flow.google.com/`）确认登录状态，并在插件中点击“导入当前 Google 账号”进行恢复。"""

            text = f"""【🚨 Flow2API 账号失效告警】

告警时间：{now_str}
账号邮箱：{email}
当前状态：{status_text}
剩余积分：{credits} 点
失效原因：{reason}

💡 处理建议：请在对应电脑浏览器中打开 Flow（https://flow.google.com/）确认登录状态，并在插件中点击“导入当前 Google 账号”进行恢复。"""

            success, msg = await self.send_wecom_message(markdown, content_text=text)
            if success:
                self._notified_expired_tokens[token_id] = (now_ts, reason)
                debug_logger.log_info(f"[WEBHOOK] 成功推送账号失效单次告警: {email} - {reason}")
            else:
                debug_logger.log_warning(f"[WEBHOOK] 推送账号失效告警失败: {msg}")
            return success
        except Exception as e:
            debug_logger.log_error(f"[WEBHOOK] 处理账号失效通知异常: {e}")
            return False

    async def build_daily_report(self, as_text: bool = False) -> str:
        """构建今日生成与账号使用情况统计报告（支持 Markdown 或纯文本）"""
        stats = await self.db.get_today_summary_stats()
        overview = stats.get("overview", {})
        accounts_usage = stats.get("accounts_usage", [])
        all_tokens = stats.get("all_tokens", [])
        today_video_credits = stats.get("today_video_credits", 0)

        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        today_str = now.strftime("%Y-%m-%d")

        img_s = overview.get("img_success", 0)
        img_f = overview.get("img_fail", 0)
        img_total = img_s + img_f
        img_rate = f"{(img_s / img_total * 100):.1f}%" if img_total > 0 else "100.0%"

        vid_s = overview.get("vid_success", 0)
        vid_f = overview.get("vid_fail", 0)
        vid_total = vid_s + vid_f
        vid_rate = f"{(vid_s / vid_total * 100):.1f}%" if vid_total > 0 else "100.0%"

        tot_s = overview.get("total_success", 0)
        tot_f = overview.get("total_fail", 0)
        tot_req = overview.get("total_requests", 0)
        tot_rate = f"{(tot_s / tot_req * 100):.1f}%" if tot_req > 0 else "100.0%"

        # 账号统计
        total_tokens = len(all_tokens)
        active_tokens = sum(1 for t in all_tokens if t.get("is_active"))
        abnormal_tokens = total_tokens - active_tokens
        total_credits = sum(t.get("credits", 0) for t in all_tokens)

        if as_text:
            # 纯文本格式
            account_lines = []
            for idx, acc in enumerate(accounts_usage):
                email = acc.get("email") or "未知账号"
                s_img = acc.get("img_success", 0)
                f_img = acc.get("img_fail", 0)
                s_vid = acc.get("vid_success", 0)
                f_vid = acc.get("vid_fail", 0)
                tot = acc.get("total_requests", 0)
                creds = acc.get("credits", 0)
                status_tag = "活跃" if acc.get("is_active") else "禁用"
                line = (
                    f"{idx + 1}. {email} ({status_tag} | 余额: {creds} 点)\n"
                    f"   今日调用: 共 {tot} 次 (图片: 成功 {s_img}/失败 {f_img} | 视频: 成功 {s_vid}/失败 {f_vid})"
                )
                account_lines.append(line)

            account_section = "\n".join(account_lines[:15]) if account_lines else "- 今日暂无生成调用记录"

            abnormal_lines = []
            for t in all_tokens:
                if not t.get("is_active"):
                    reason = t.get("ban_reason") or t.get("last_st_refresh_result") or "已处于禁用状态"
                    abnormal_lines.append(f"- {t.get('email')}：{reason}")

            abnormal_section = ""
            if abnormal_lines:
                abnormal_section = "\n\n【⚠️ 异常/失效账号提醒】\n" + "\n".join(abnormal_lines)

            return f"""【📊 Flow2API 每日运行情况汇报】

统计日期：{today_str}
汇报时间：{now_str}

【今日生成概况】
- 图片生成：成功 {img_s} 次 | 失败 {img_f} 次 (成功率 {img_rate})
- 视频生成：成功 {vid_s} 次 | 失败 {vid_f} 次 (消耗约 {today_video_credits} 积分)
- 总请求数：共 {tot_req} 次调用 (整体成功率 {tot_rate})

【账号状态概览】
- 账号总数：{total_tokens} 个 (活跃: {active_tokens} | 异常/过期: {abnormal_tokens})
- 总剩余积分：{total_credits} 点

【今日各账号调用统计】
{account_section}{abnormal_section}"""

        # Markdown 格式
        account_lines = []
        medals = ["🥇", "🥈", "🥉"]
        for idx, acc in enumerate(accounts_usage):
            rank = medals[idx] if idx < len(medals) else f"{idx + 1}."
            email = acc.get("email") or "未知账号"
            s_img = acc.get("img_success", 0)
            f_img = acc.get("img_fail", 0)
            s_vid = acc.get("vid_success", 0)
            f_vid = acc.get("vid_fail", 0)
            tot = acc.get("total_requests", 0)
            creds = acc.get("credits", 0)
            status_tag = '<font color="info">活跃</font>' if acc.get("is_active") else '<font color="warning">禁用</font>'
            
            line = (
                f"{rank} **{email}** ({status_tag} | 余额: {creds} 点)\n"
                f"> 今日调用: 共 **{tot}** 次 (图片: 成功 {s_img}/失败 {f_img} | 视频: 成功 {s_vid}/失败 {f_vid})"
            )
            account_lines.append(line)

        account_section = "\n\n".join(account_lines[:15]) if account_lines else "> 今日暂无生成调用记录"

        abnormal_lines = []
        for t in all_tokens:
            if not t.get("is_active"):
                reason = t.get("ban_reason") or t.get("last_st_refresh_result") or "已处于禁用状态"
                abnormal_lines.append(f"- **{t.get('email')}**：<font color=\"warning\">{reason}</font>")

        abnormal_section = ""
        if abnormal_lines:
            abnormal_section = "\n\n#### ⚠️ 异常/失效账号提醒\n" + "\n".join(abnormal_lines)

        return f"""### 📊 Flow2API 每日运行情况汇报

> **统计日期**：{today_str}
> **汇报时间**：{now_str}

#### 📈 今日生成概况
- **图片生成**：成功 <font color="info">{img_s}</font> 次 | 失败 <font color="warning">{img_f}</font> 次（成功率 {img_rate}）
- **视频生成**：成功 <font color="info">{vid_s}</font> 次 | 失败 <font color="warning">{vid_f}</font> 次（消耗约 {today_video_credits} 积分）
- **总请求数**：共 **{tot_req}** 次调用（整体成功率 {tot_rate}）

#### 👥 账号状态概览
- **账号总数**：{total_tokens} 个（活跃: <font color="info">{active_tokens}</font> | 异常/过期: <font color="warning">{abnormal_tokens}</font>）
- **总剩余积分**：{total_credits} 点

#### 🏆 今日各账号调用统计（按调用量排序）
{account_section}{abnormal_section}"""

    async def send_daily_report(self, custom_url: Optional[str] = None, custom_msg_type: Optional[str] = None) -> tuple[bool, str]:
        """构建并发送每日生成与账号汇报"""
        try:
            md_report = await self.build_daily_report(as_text=False)
            text_report = await self.build_daily_report(as_text=True)
            return await self.send_wecom_message(
                md_report,
                content_text=text_report,
                custom_url=custom_url,
                custom_msg_type=custom_msg_type
            )
        except Exception as e:
            debug_logger.log_error(f"[WEBHOOK] 生成并发送每日汇报异常: {e}")
            return False, str(e)

    async def check_all_tokens_health(self):
        """巡检所有账号，如果发现有失效或过期的账号触发单次告警；已恢复健康的账号自动清除告警标记"""
        try:
            tokens = await self.db.get_all_tokens()
            now_utc = datetime.now(timezone.utc)
            for token in tokens:
                is_active = bool(token.is_active)
                is_at_valid = False
                if token.at_expires:
                    at_exp = token.at_expires if token.at_expires.tzinfo else token.at_expires.replace(tzinfo=timezone.utc)
                    if at_exp > now_utc:
                        is_at_valid = True

                if is_active and is_at_valid:
                    # 账号健康正常，清除失效通知标记（未来若再次失效可重新触发一次告警）
                    self.mark_token_recovered(token.id)
                else:
                    # 账号失效，触发告警（内部已有去重拦截，失效期间仅发一次，绝不重复提醒）
                    if not is_active:
                        reason = token.ban_reason or token.last_st_refresh_result or "账号已处于禁用状态"
                        await self.notify_token_expired(token.id, f"账号已被禁用: {reason}", token_obj=token)
                    elif not is_at_valid:
                        await self.notify_token_expired(token.id, "Access Token 已过期且未恢复", token_obj=token)
        except Exception as e:
            debug_logger.log_error(f"[WEBHOOK] 账号巡检异常: {e}")

    async def start_scheduler(self):
        """启动后台定时任务（每日定时汇报 + 定期巡检）"""
        self._running = True
        debug_logger.log_info("[WEBHOOK] 定时汇报与账号监控调度任务已启动")
        import time

        last_check_time = 0.0
        while self._running:
            try:
                await asyncio.sleep(20)
                cfg = await self.db.get_webhook_config()
                if not cfg.enabled or not cfg.wecom_webhook_url:
                    continue

                now = datetime.now()
                now_hm = now.strftime("%H:%M")
                today_str = now.strftime("%Y-%m-%d")

                # 1. 每日定时汇报检查
                if cfg.daily_report_enabled and cfg.daily_report_time:
                    target_hm = cfg.daily_report_time.strip()
                    if now_hm == target_hm and self._last_daily_report_date != today_str:
                        debug_logger.log_info(f"[WEBHOOK] 触发定时每日汇报: {now_hm}")
                        success, msg = await self.send_daily_report()
                        if success:
                            self._last_daily_report_date = today_str
                            debug_logger.log_info("[WEBHOOK] 定时每日汇报发送成功")
                        else:
                            debug_logger.log_warning(f"[WEBHOOK] 定时每日汇报发送失败: {msg}")

                # 2. 定期巡检账号健康（每 5 分钟巡检一次）
                if time.time() - last_check_time >= 300:
                    last_check_time = time.time()
                    if cfg.notify_on_expired:
                        await self.check_all_tokens_health()

            except asyncio.CancelledError:
                break
            except Exception as e:
                debug_logger.log_error(f"[WEBHOOK] 调度循环异常: {e}")
                await asyncio.sleep(5)

    def stop_scheduler(self):
        """停止调度任务"""
        self._running = False


def get_webhook_service(db: Optional[Database] = None) -> WebhookService:
    return WebhookService.get_instance(db)

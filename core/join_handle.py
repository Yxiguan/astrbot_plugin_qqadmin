import json
from pathlib import Path

from aiocqhttp import CQHttp

from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

from ..utils import get_nickname, get_reply_message_str


class GroupJoinData:
    def __init__(self, path: Path, config: dict):
        self.path = path
        # 总数据
        self._cfg: dict[str, dict] = {}
        self._load()
        # 默认配置
        self.default_cfg = {
            "switch": config["default_switch"],
            "accept_keywords": [],
            "reject_keywords": [],
            "min_level": config["default_min_level"],
            "max_time": config["default_max_time"],
            "block_ids": [],
        }

    # ---------- 私有工具 ----------
    def _load(self):
        if not self.path.exists():
            self.save()
            return
        try:
            with self.path.open(encoding="utf-8") as f:
                self._cfg = json.load(f)
        except Exception as e:
            logger.error(f"加载失败: {e}")
            self.save()

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", encoding="utf-8") as f:
                json.dump(self._cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存失败: {e}")

    def ensure_group(self, group_id: str) -> None:
        """群聊没有配置时创建默认配置并落盘"""
        if group_id not in self._cfg:
            self._cfg[group_id] = self.default_cfg.copy()
            self.save()

    # ---------- 对外接口 ----------
    def get(self, group_id: str) -> dict:
        """返回该群配置（无则返回空 dict）"""
        return self._cfg.get(group_id, {})

    def set(self, group_id: str, **kwargs) -> None:
        """直接覆写给定字段"""
        self.ensure_group(group_id)
        self._cfg.setdefault(group_id, {}).update(kwargs)
        self.save()

    def remove(self, group_id: str) -> None:
        """删除整个群配置"""
        self._cfg.pop(group_id, None)
        self.save()

    # ---------- 快捷只读访问 ----------
    def get_switch(self, group_id: str) -> bool:
        return self.get(group_id).get("switch", False)

    def get_accept_keywords(self, group_id: str) -> list[str]:
        return self.get(group_id).get("accept_keywords", [])

    def get_reject_keywords(self, group_id: str) -> list[str]:
        return self.get(group_id).get("reject_keywords", [])

    def get_min_level(self, group_id: str) -> int:
        return self.get(group_id).get("min_level", 0)

    def get_max_time(self, group_id: str) -> int:
        return self.get(group_id).get("max_time", 0)

    def get_block_ids(self, group_id: str) -> list[str]:
        return self.get(group_id).get("block_ids", [])

    # ---------- 快捷覆写 ----------
    def set_switch(self, group_id: str, on: bool) -> None:
        self.set(group_id, switch=on)

    def set_accept_keywords(self, group_id: str, kws: list[str]) -> None:
        self.set(group_id, accept_keywords=kws)

    def set_reject_keywords(self, group_id: str, kws: list[str]) -> None:
        self.set(group_id, reject_keywords=kws)

    def set_min_level(self, group_id: str, level: int) -> None:
        self.set(group_id, min_level=level)

    def set_max_time(self, group_id: str, times: int) -> None:
        self.set(group_id, max_time=times)

    def set_block_ids(self, group_id: str, uids: list[str]) -> None:
        self.set(group_id, block_ids=uids)


class JoinHandle:
    DB_VERSION = 2

    def __init__(self, config: AstrBotConfig, data_dir: Path, admin_ids: list[str]):
        self.jconf = config["join_config"]
        self.admin_ids: list[str] = admin_ids
        json_file = data_dir / f"group_join_data_v{self.DB_VERSION}.json"
        self.db = GroupJoinData(json_file, self.jconf)
        # 加群失败次数缓存（key 用 f"{group_id}_{user_id}"）
        self._fail: dict[str, int] = {}

    async def _send_admin(self, client: CQHttp, message: str):
        """向bot管理员发送私聊消息"""
        for admin_id in self.admin_ids:
            if admin_id.isdigit():
                try:
                    await client.send_private_msg(
                        user_id=int(admin_id), message=message
                    )
                except Exception as e:
                    logger.error(f"无法发送消息给bot管理员：{e}")

    @staticmethod
    def _parse_mode(mode: str | bool | None):
        """解析模式"""
        match mode:
            case "开", "开启", "on", "true", "1", True:
                return True
            case "关", "关闭", "off", "false", "0", False:
                return False
            case _:
                return None

    # -----------修改配置-----------------

    async def handle_join_review(
        self, event: AiocqhttpMessageEvent, mode_str: str | bool | None
    ):
        """设置/查看本群进群审核开关"""
        gid = event.get_group_id()
        mode = self._parse_mode(mode_str)
        if mode is True:
            self.db.set_switch(gid, True)
            await event.send(event.plain_result("已开启本群进群审核"))
        elif mode is False:
            self.db.set_switch(gid, False)
            await event.send(event.plain_result("已关闭本群进群审核"))
        else:
            status = "开启" if self.db.get_switch(gid) else "关闭"
            await event.send(event.plain_result(f"本群进群审核：{status}"))

    async def handle_accept_keyword(self, event: AiocqhttpMessageEvent):
        """设置/查看自动批准进群的关键词"""
        gid = event.get_group_id()
        if keywords := event.message_str.removeprefix("进群白词").strip().split():
            self.db.set_accept_keywords(gid, keywords)
            await event.send(
                event.plain_result(f"本群的进群关键词已设置为：{keywords}")
            )
        else:
            keywords = self.db.get_accept_keywords(gid)
            await event.send(event.plain_result(f"本群的进群关键词：{keywords}"))

    async def handle_reject_keywords(self, event: AiocqhttpMessageEvent):
        """设置/查看进群黑名单关键词"""
        gid = event.get_group_id()
        if keywords := event.message_str.removeprefix("进群黑词").strip().split():
            self.db.set_reject_keywords(gid, keywords)
            await event.send(event.plain_result(f"新增进群黑名单关键词：{keywords}"))
        else:
            keywords = self.db.get_reject_keywords(gid)
            await event.send(event.plain_result(f"本群的进群黑名单关键词：{keywords}"))

    async def handle_level_threshold(
        self, event: AiocqhttpMessageEvent, level: int | None
    ):
        """设置进群等级门槛"""
        gid = event.get_group_id()
        if isinstance(level, int):
            self.db.set_min_level(gid, level)
            msg = (
                f"已设置本群进群等级门槛：{level}级"
                if level > 0
                else "已解除本群的进群等级限制"
            )
            await event.send(event.plain_result(msg))
        else:
            level = self.db.get_min_level(gid)
            await event.send(event.plain_result(f"本群的进群等级门槛: {level}级"))

    async def handle_join_time(self, event: AiocqhttpMessageEvent, time: int | None):
        """设置最大进群次数"""
        gid = event.get_group_id()
        if isinstance(time, int):
            self.db.set_max_time(gid, time)
            msg = (
                f"已限制本群进群次数：{time}次"
                if time > 0
                else "已解除本群的进群等级限制"
            )
            await event.send(event.plain_result(msg))
        else:
            time = self.db.get_max_time(gid)
            await event.send(event.plain_result(f"本群的进群最多可尝试 {time} 次"))

    async def handle_reject_ids(self, event: AiocqhttpMessageEvent):
        """设置/查看进群黑名单"""
        gid = event.get_group_id()
        if ids := event.message_str.removeprefix("进群黑名单").strip().split():
            self.db.set_block_ids(gid, ids)
        else:
            ids = self.db.get_block_ids(gid)
            await event.send(event.plain_result(f"本群的进群黑名单：{ids}"))

    # ---------辅助函数-----------------

    def should_approve(
        self,
        group_id: str,
        user_id: str,
        comment: str | None = None,
        user_level: int | None = None,
    ) -> tuple[bool, str]:
        """判断是否让该用户入群，返回原因"""
        # 黑名单用户
        if group_id in self.db.get(group_id) and user_id in self.db.get_block_ids(
            group_id
        ):
            return False, "黑名单用户"

        # QQ等级过低
        min_level = self.db.get_min_level(group_id)
        if min_level > 0 and user_level and user_level < min_level:
            return False, f"QQ等级过低({user_level}<{min_level})"

        # 命中进群黑词
        if comment:
            lower_comment = comment.lower()
            if any(
                rk.lower() in lower_comment
                for rk in self.db.get_reject_keywords(group_id)
            ):
                block_ids = list(set(self.db.get_block_ids(group_id)) | {user_id})
                self.db.set_block_ids(group_id, block_ids)
                return False, "命中进群黑词"

        # 最大失败次数（考虑到只是防爆破，存内存里足矣，重启清零）
        max_fail = self.db.get_max_time(group_id)
        if max_fail > 0:
            key = f"{group_id}_{user_id}"
            self._fail[key] = self._fail.get(key, 0) + 1
            if self._fail[key] > max_fail:
                return False, f"进群尝试次数已达上限({max_fail}次)"

        # 命中进群白词
        if (
            comment
            and group_id in self.db.get_accept_keywords(group_id)
            and any(
                ak.lower() in comment.lower()
                for ak in self.db.get_accept_keywords(group_id)
            )
        ):
            return True, "验证通过"

        # 未包含进群关键词
        return True, ""

    # ---------处理事件-----------------

    async def event_monitoring(self, event: AiocqhttpMessageEvent):
        """监听进群/退群事件"""
        raw = getattr(event.message_obj, "raw_message", None)
        if not isinstance(raw, dict):
            return

        group_id: str = str(raw.get("group_id", ""))

        # 进群审核总开关
        if not self.db.get_switch(group_id):
            return
        self.db.ensure_group(group_id)

        client = event.bot
        user_id: str = raw.get("user_id", "")

        # 进群申请事件
        if (
            raw.get("post_type") == "request"
            and raw.get("request_type") == "group"
            and raw.get("sub_type") == "add"
        ):
            comment = raw.get("comment")
            flag = raw.get("flag", "")
            stranger_info = await client.get_stranger_info(user_id=int(user_id))
            nickname = stranger_info.get("nickname") or "未知昵称"
            user_level = stranger_info.get("qqLevel") or stranger_info.get("level")

            # 生成并发送通知
            reply = f"【进群申请】批准/驳回：\n昵称：{nickname}\nQQ：{user_id}\nflag：{flag}"
            if user_level is not None:
                reply += f"\n等级：{user_level}"
            if comment:
                reply += f"\n{comment}"
            if self.jconf["admin_audit"]:
                await self._send_admin(client, reply)
            else:
                await event.send(event.plain_result(reply))

            # 判断是否通过
            approve, reason = self.should_approve(
                group_id, user_id, comment, user_level
            )
            await client.set_group_add_request(
                flag=flag,
                sub_type="add",
                approve=approve,
                reason=reason if not approve else "",
            )
            msg = f"已自动{'批准' if approve else '驳回'}: {reason}"
            await event.send(event.plain_result(msg))

        # 主动退群事件
        elif (
            self.jconf["leave_notify"]
            and raw.get("post_type") == "notice"
            and raw.get("notice_type") == "group_decrease"
            and raw.get("sub_type") == "leave"
        ):
            nickname = await get_nickname(event, user_id)
            msg = f"{nickname}({user_id}) 主动退群了"
            if self.jconf["leave_block"]:
                block_ids = self.db.get_block_ids(group_id) + [user_id]
                self.db.set_block_ids(group_id, block_ids)
                msg += "，已拉进黑名单"
            await event.send(event.plain_result(msg))

        # 进群欢迎、禁言
        elif (
            raw.get("notice_type") == "group_increase"
            and str(user_id) != event.get_self_id()
        ):
            # 进群欢迎
            if self.jconf["welcome_template"]:
                welcome_template: str = self.jconf["welcome_template"]
                nickname = await get_nickname(event, user_id)
                welcome = welcome_template.format(nickname=nickname)
                await event.send(event.plain_result(welcome))
            # 进群禁言
            if self.jconf["ban_time"] > 0:
                try:
                    await client.set_group_ban(
                        group_id=int(group_id),
                        user_id=int(user_id),
                        duration=self.jconf["ban_time"],
                    )
                except Exception:
                    pass

    async def agree_add_group(self, event: AiocqhttpMessageEvent, extra: str = ""):
        """批准进群申请"""
        reply = await self.set_approve(event=event, extra=extra, approve=True)
        if reply:
            await event.send(event.plain_result(reply))

    async def refuse_add_group(self, event: AiocqhttpMessageEvent, extra: str = ""):
        """驳回进群申请"""
        reply = await self.set_approve(event=event, extra=extra, approve=False)
        if reply:
            await event.send(event.plain_result(reply))

    @staticmethod
    async def set_approve(
        event: AiocqhttpMessageEvent, extra: str = "", approve: bool = True
    ) -> str | None:
        """处理进群申请"""
        text = get_reply_message_str(event)
        if not text:
            return "未引用任何【进群申请】"
        lines = text.split("\n")
        if "【进群申请】" in text and len(lines) >= 4:
            nickname = lines[1].split("：")[1]  # 第2行冒号后文本为nickname
            flag = lines[3].split("：")[1]  # 第4行冒号后文本为flag
            try:
                await event.bot.set_group_add_request(
                    flag=flag, sub_type="add", approve=approve, reason=extra
                )
                if approve:
                    reply = f"已同意{nickname}进群"
                else:
                    reply = f"已拒绝{nickname}进群" + (
                        f"\n理由：{extra}" if extra else ""
                    )
                return reply
            except Exception:
                return "这条申请处理过了或者格式不对"

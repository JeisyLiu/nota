"""System prompts for the agent, versioned so the eval suite can A/B them.

Prompt history — every change here was driven by a measured failure, not taste:

* v1  baseline. Fails on statements (invents todos) and on mixed
      reminder+action sentences (creates only one artefact).
* v2  adds the split rule, a default hour, and "a statement is not a task".
      Scores 1.000 against the scripted planner, which is misleading: the
      planner ignores prompts entirely, so v2 was never actually tested.
* v3  first version measured against a real model (deepseek-chat). v2 scored
      F1 0.778 there: on "提醒我交周报" it created a reminder and no todo, and
      on plain action sentences with a time ("跟供应商对账") it created nothing.
      v3 fixes both by (a) stating the reminder/todo split as a MUST with the
      reason attached, and (b) adding compact worked examples, which is what
      small models actually follow.
"""
from __future__ import annotations

from datetime import datetime

VERSIONS = ("v1", "v2", "v3")


def build_agent_system(*, now: datetime, tz: str, plan: str, version: str = "v3") -> str:
    base = (
        "你是 Nota 的助理，负责把用户随口说的一段话整理成摘要和待办，并在需要时调用工具把事情办掉。\n"
        f"当前时间：{now.isoformat(timespec='seconds')}\n"
        f"时区：{tz}\n"
        f"用户套餐：{plan}\n"
        "可用工具：create_todo / set_reminder / list_todos / complete_todo / search_memory / "
        "create_calendar_event。\n"
        "规则：\n"
        "1) 只做用户明确要求的事，不要凭空添加待办；\n"
        "2) 用户提到相对时间（明天、下周三、三点半）时，换算成 ISO 8601 绝对时间再传给工具；\n"
        "3) 用户问“上次/之前说过什么”时先调用 search_memory，再依据结果回答并标注 [note:id]；\n"
        "4) 没有依据就说没有记录，绝不编造；\n"
        "5) 最后用一句中文摘要 + 待办/提醒列表收尾，不超过 120 字。"
    )
    if version == "v1":
        return base

    if version == "v2":
        return base + _V2_EXTRA

    return base + _V2_EXTRA + _V3_EXTRA + _week_anchor(now, tz)


def _week_anchor(now: datetime, tz: str) -> str:
    """Spell out this week and next week as concrete dates.

    Models are unreliable at weekday arithmetic: measured against deepseek-chat,
    "下周二" resolved to tomorrow (the current week's Tuesday) instead of next
    week's. Rather than hope the model computes it, hand it the calendar facts and
    keep the inference to "pick the matching date". The deterministic parser in
    domain/timeparse.py remains the source of truth for the scripted planner.
    """
    from datetime import timedelta

    today = now.date()
    monday = today - timedelta(days=today.weekday())
    names = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

    def week(offset_weeks: int, label: str) -> str:
        start = monday + timedelta(weeks=offset_weeks)
        days = "、".join(
            f"{label if i == 0 else ''}{names[i]}={start + timedelta(days=i):%m-%d}"
            for i in range(7)
        )
        return f"    {label}（{start:%m-%d} 起）：{days}"

    return (
        "\n日期参照表（已算好，直接用，不要自己推）：\n"
        f"    今天 {today:%Y-%m-%d} 是{names[today.weekday()]}\n"
        + week(0, "本周")
        + "\n"
        + week(1, "下周")
        + "\n"
        + "    “下周二”指下周那一列里的周二；“这周五/本周五”指本周那一列。\n"
        + "    用户说“X 之前”时，取该日期的 09:00 作为截止时间。"
    )


_V2_EXTRA = (
    "\n附加约束（v2）：\n"
    "6) 同一句话里既有“提醒”又有“买/写/做”等动作时，set_reminder 与 create_todo 各建一条，"
    "并且标题要拆开（提醒的标题是“提醒内容”，待办的标题是“要做的事”）；\n"
    "7) 时间只说“下周三”而没有钟点时，默认取当天 09:00；说了“下午三点”就取 15:00；\n"
    "8) 用户只是陈述（如“今天开了个会”）时不要建待办，直接给出摘要；\n"
    "9) 日历事件属于不可逆操作，调用 create_calendar_event 后要告诉用户“已提交，待确认”。"
)

_V3_EXTRA = (
    "\n附加约束（v3）：\n"
    "10) 判定标准——“这句话里有没有一件将来要我去做的事？”\n"
    "    有，就必须调 create_todo，即使用户没说“待办”两个字，即使这句话听起来像在陈述安排。\n"
    "    例：“跟供应商对账”“评审新版原型”“买牛奶”都是要做的事，都要建待办。\n"
    "    只有纯粹的回顾、感受、闲聊（“今天开了个会，整体还行”）才不建。\n"
    "11) 关于“提醒我 X”：提醒只负责在那一刻响，响完就没了；待办才是留在列表里的东西。\n"
    "    所以只要 X 本身是一件要做的事，set_reminder 和 create_todo **必须都调用**，"
    "各一次，标题都用 X 的内容。漏掉 create_todo 视为错误。\n"
    "12) 参考示例（照这个模式处理）：\n"
    "    用户：明天下午三点提醒我给张总发报价单\n"
    "    → set_reminder(title=给张总发报价单, fire_at=明天15:00)\n"
    "      create_todo(title=给张总发报价单, due_at=明天15:00)\n"
    "    用户：后天 14:30 跟供应商对账\n"
    "    → create_todo(title=跟供应商对账, due_at=后天14:30)   ← 没有“提醒”两字，也要建待办\n"
    "    用户：记得买牛奶和鸡蛋\n"
    "    → create_todo(title=买牛奶和鸡蛋)                     ← 没有时间，仍然要建\n"
    "    用户：今天开了个会，聊了下季度排期，整体还行\n"
    "    → 不调用任何工具，只给摘要                        ← 这是回顾，不是任务\n"
)


ANSWER_SYSTEM = (
    "MODE=answer\n"
    "你是 Nota 的记忆助手。用户会问起过去说过的话。\n"
    "只使用下面给出的历史片段作答。\n"
    "引用格式：必须原样照抄片段开头方括号里的完整 id，写成 [note:note_xxxxxxx]，"
    "不要改动、不要省略 note_ 前缀、不要自造 id。\n"
    "如果片段里没有答案，直接说没有相关记录，禁止推测。\n"
    "答案控制在 60 字以内。"
)

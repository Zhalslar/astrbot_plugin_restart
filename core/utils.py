# utils.py


def cron_to_human(cron: str) -> str:
    """
    将 5 段 cron（分 时 日 月 周）转换为中文易读描述
    """
    parts = cron.strip().split()
    if len(parts) != 5:
        raise ValueError("Cron 表达式必须是 5 段（分 时 日 月 周）")

    minute, hour, day, month, week = parts

    def parse_field(val, unit, names=None):
        if val == "*":
            return f"每{unit}"
        if val.startswith("*/"):
            return f"每{val[2:]}{unit}"
        if "," in val:
            items = val.split(",")
            return "、".join(
                names.get(i, f"{i}{unit}") if names else f"{i}{unit}" for i in items
            )
        if "-" in val:
            start, end = val.split("-")
            if names:
                return f"{names[start]}至{names[end]}"
            return f"{start}到{end}{unit}"
        return names.get(val, f"{val}{unit}") if names else f"{val}{unit}"

    week_names = {
        "0": "周日",
        "1": "周一",
        "2": "周二",
        "3": "周三",
        "4": "周四",
        "5": "周五",
        "6": "周六",
    }

    desc = []

    # 周
    if week != "*":
        desc.append(parse_field(week, "", week_names))

    # 月
    if month != "*":
        desc.append(parse_field(month, "月"))

    # 日
    if day != "*":
        desc.append(parse_field(day, "日"))
    elif week == "*":
        desc.append("每天")

    # 时间
    if hour == "*" and minute == "*":
        desc.append("每分钟")
    else:
        time_desc = []
        if hour != "*":
            time_desc.append(parse_field(hour, "点"))
        if minute != "*":
            time_desc.append(parse_field(minute, "分"))
        desc.append(" ".join(time_desc))

    return " ".join(desc)



def get_memory_info(decimal_places=1):
    """
    获取当前设备内存情况，支持自定义小数位数

    Args:
        decimal_places (int): 小数位数，默认为1位

    Returns:
        str: 已用内存/总内存(百分比) 格式，如 "8.5GB/16.0GB(53.2%)"
    """
    import psutil
    # 获取内存信息
    memory = psutil.virtual_memory()

    # 计算已用内存 (总内存 - 可用内存)
    total_memory = memory.total
    used_memory = total_memory - memory.available

    # 转换为GB单位
    total_gb = total_memory / (1024**3)
    used_gb = used_memory / (1024**3)

    # 计算使用百分比
    usage_percent = (used_memory / total_memory) * 100

    # 格式化输出，使用指定的小数位数
    format_str = f"{{:.{decimal_places}f}}GB/{{:.{decimal_places}f}}GB({{:.1f}}%)"
    return format_str.format(used_gb, total_gb, usage_percent)

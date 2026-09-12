# [AGENT_CHANGE_BEGIN] 2026-09-07 104-MVP协议常量
"""IEC 60870-5-104 常量与类型标识。"""
from __future__ import annotations

from enum import IntEnum


START_BYTE = 0x68
DEFAULT_PORT = 2404


class Cot(IntEnum):
    PERIODIC = 1
    BACKGROUND = 2
    SPONTANEOUS = 3
    INIT = 4
    REQUEST = 5
    ACTIVATION = 6
    ACT_CON = 7
    DEACTIVATION = 8
    DEACT_CON = 9
    ACT_TERM = 10
    INTERROGATED_BY_STATION = 20
    UNKNOWN_TYPE = 44
    UNKNOWN_COT = 45
    UNKNOWN_CA = 46
    UNKNOWN_IOA = 47


class TypeId(IntEnum):
    M_SP_NA_1 = 1
    M_DP_NA_1 = 3
    M_ST_NA_1 = 5
    M_BO_NA_1 = 7
    M_ME_NA_1 = 9
    M_ME_NB_1 = 11
    M_ME_NC_1 = 13
    M_IT_NA_1 = 15
    M_SP_TB_1 = 30
    M_DP_TB_1 = 31
    M_ME_TF_1 = 36
    C_SC_NA_1 = 45
    C_DC_NA_1 = 46
    C_RC_NA_1 = 47
    C_SE_NA_1 = 48
    C_SE_NB_1 = 49
    C_SE_NC_1 = 50
    C_IC_NA_1 = 100
    C_CI_NA_1 = 101
    C_RD_NA_1 = 102
    C_CS_NA_1 = 103
    # [AGENT_CHANGE_BEGIN] 2026-09-12 复位进程命令
    C_RP_NA_1 = 105  # 复位进程命令
    # [AGENT_CHANGE_END] 2026-09-12 复位进程命令
    # [AGENT_CHANGE_BEGIN] 2026-09-12 平衡101对齐KW-2200延时获得
    C_CD_NA_1 = 106  # 延时获得命令
    # [AGENT_CHANGE_END] 2026-09-12 平衡101对齐KW-2200延时获得


TYPE_NAMES = {
    1: "单点遥信 M_SP_NA_1",
    3: "双点遥信 M_DP_NA_1",
    5: "步位置 M_ST_NA_1",
    9: "归一化遥测 M_ME_NA_1",
    11: "标度化遥测 M_ME_NB_1",
    13: "短浮点遥测 M_ME_NC_1",
    15: "累计量 M_IT_NA_1",
    30: "带时标单点 M_SP_TB_1",
    31: "带时标双点 M_DP_TB_1",
    36: "带时标浮点 M_ME_TF_1",
    45: "单点遥控 C_SC_NA_1",
    46: "双点遥控 C_DC_NA_1",
    47: "步调节 C_RC_NA_1",
    48: "归一化设点 C_SE_NA_1",
    49: "标度化设点 C_SE_NB_1",
    50: "短浮点设点 C_SE_NC_1",
    # ---- 配网自动化扩展/专用类型 ----
    55: "预置/激活参数命令(短浮点) C_SP_NA_1",
    100: "总召唤 C_IC_NA_1",
    101: "计数量召唤 C_CI_NA_1",
    102: "读命令 C_RD_NA_1",
    103: "时钟同步 C_CS_NA_1",
    # [AGENT_CHANGE_BEGIN] 2026-09-12 平衡101对齐KW-2200延时获得
    106: "延时获得 C_CD_NA_1",
    # [AGENT_CHANGE_END] 2026-09-12 平衡101对齐KW-2200延时获得
    105: "复位进程命令 C_RP_NA_1",
    108: "读参数命令(短浮点) C_RS_NA_1",
    136: "特殊应用(专用范围) 定值类",
    200: "切换定值区 C_SR_NA_1",
    201: "读定值区号 C_RR_NA_1",
    202: "读参数和定值 C_RS_NA_1",
    203: "写参数和定值 C_WS_NA_1",
    210: "文件传输",
}

# 传送原因（COT）中文名称：报文监视注释用
COT_NAMES = {
    0: "未知",
    1: "周期",
    2: "背景扫描",
    3: "突发",
    4: "初始化",
    5: "请求",
    6: "激活",
    7: "激活确认",
    8: "停止激活(撤销)",
    9: "停止激活确认",
    10: "激活终止",
    11: "未知类型",
    12: "未知传送原因",
    13: "未知公共地址",
    14: "未知信息对象地址",
    20: "响应站召唤",
    21: "响应第1组召唤",
    22: "响应第2组召唤",
    23: "响应第3组召唤",
    24: "响应第4组召唤",
    25: "响应第5组召唤",
    26: "响应第6组召唤",
    27: "响应第7组召唤",
    28: "响应第8组召唤",
    29: "响应第9组召唤",
    30: "响应第10组召唤",
    31: "响应第11组召唤",
    32: "响应第12组召唤",
    33: "响应第13组召唤",
    34: "响应第14组召唤",
    35: "响应第15组召唤",
    36: "响应第16组召唤",
    44: "未知类型",
    45: "未知传送原因",
    46: "未知公共地址",
    47: "激活终止(细则47)",
}


def cot_name(cot: int) -> str:
    return COT_NAMES.get(int(cot), str(cot))


# [AGENT_CHANGE_END] 2026-09-07 104-MVP协议常量

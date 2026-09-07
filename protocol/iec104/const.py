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
    C_CS_NA_1 = 103


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
    100: "总召唤 C_IC_NA_1",
    101: "计数量召唤 C_CI_NA_1",
    103: "时钟同步 C_CS_NA_1",
}


# [AGENT_CHANGE_END] 2026-09-07 104-MVP协议常量

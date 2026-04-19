from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from gpr_lab_pro.domain.enums import StepKind


@dataclass(frozen=True)
class ParameterSpec:
    key: str
    label: str
    default: str
    caster: Callable[[str], float] = float


@dataclass(frozen=True)
class OperationSpec:
    op_type: str
    category: str
    kind: StepKind
    menu_path: tuple[str, ...]
    title: str
    params: tuple[ParameterSpec, ...] = ()

    def parse_params(self, values: Sequence[str]) -> list[float]:
        return [spec.caster(value) for spec, value in zip(self.params, values)]


def _int_cast(value: str) -> int:
    return int(round(float(value)))


OPERATION_SPECS: tuple[OperationSpec, ...] = (
    OperationSpec("dewow", "时域处理", StepKind.TIME, ("预处理",), "Dewow", (ParameterSpec("win", "窗口长度(采样点)", "20", _int_cast),)),
    OperationSpec("t0", "时域处理", StepKind.TIME, ("预处理", "零点校正"), "最大值对齐", (ParameterSpec("time", "目标时间(ns)", "1.0"),)),
    OperationSpec("t0_fb", "时域处理", StepKind.TIME, ("预处理", "零点校正"), "首波对齐", (
        ParameterSpec("time", "目标时间(ns)", "1.0"),
        ParameterSpec("k", "阈值系数K", "5"),
    )),
    OperationSpec("phase_correction", "频域处理", StepKind.FREQUENCY, ("预处理", "频域处理"), "频域相位校正", (
        ParameterSpec("f_start", "起始频率(MHz)", "500"),
        ParameterSpec("f_end", "终止频率(MHz)", "2500"),
    )),
    OperationSpec("interf_active", "频域处理", StepKind.FREQUENCY, ("预处理", "干扰抑制"), "有源干扰抑制", (
        ParameterSpec("thr", "功率阈值(dB)", "10"),
    )),
    OperationSpec("interf_passive", "频域处理", StepKind.FREQUENCY, ("预处理", "干扰抑制"), "无源干扰抑制", (
        ParameterSpec("thr", "门限(dB)", "10"),
        ParameterSpec("win", "局部窗口(bin)", "31", _int_cast),
        ParameterSpec("ext", "扩展(bin)", "2", _int_cast),
        ParameterSpec("hit", "最小命中比例", "0.15"),
        ParameterSpec("is_freq", "输入已是频域(1/0)", "0", _int_cast),
    )),
    OperationSpec("bg_mean", "时域处理", StepKind.TIME, ("去背景",), "全局均值去背景"),
    OperationSpec("bg_median", "时域处理", StepKind.TIME, ("去背景",), "全局中值去背景"),
    OperationSpec("bg_move", "时域处理", StepKind.TIME, ("去背景",), "滑动均值去背景", (ParameterSpec("win", "窗口(道)", "50", _int_cast),)),
    OperationSpec("bg_svd", "时域处理", StepKind.TIME, ("去背景",), "SVD 去背景", (ParameterSpec("rank", "阶数", "1", _int_cast),)),
    OperationSpec("bg_rpca", "时域处理", StepKind.TIME, ("去背景",), "RPCA 去背景"),
    OperationSpec("bg_top_mute", "时域处理", StepKind.TIME, ("去背景",), "浅层切除", (ParameterSpec("time", "静音时间(ns)", "5.0"),)),
    OperationSpec("bg_freq", "频域处理", StepKind.FREQUENCY, ("去背景",), "频域去背景", (ParameterSpec("mode", "模式 1/2", "1", _int_cast),)),
    OperationSpec("bg_highpass", "时域处理", StepKind.TIME, ("去背景",), "高通去背景", (
        ParameterSpec("removal", "去除强度(%)", "100"),
        ParameterSpec("start", "开始时间(ns)", "2"),
        ParameterSpec("transition", "过渡带(ns)", "4"),
        ParameterSpec("win", "窗口长度(Trace)", "100", _int_cast),
    )),
    OperationSpec("bg_adaptive", "时域处理", StepKind.TIME, ("去背景",), "自适应去背景", (
        ParameterSpec("removal", "去除强度(%)", "100"),
        ParameterSpec("start", "开始时间(ns)", "2"),
        ParameterSpec("transition", "过渡带(ns)", "4"),
        ParameterSpec("win", "窗口长度(Trace)", "100", _int_cast),
        ParameterSpec("surface", "表面搜索窗(ns)", "20"),
        ParameterSpec("protect_start", "保护起点(ns)", "0"),
        ParameterSpec("protect_end", "保护终点(ns)", "30"),
    )),
    OperationSpec("gain_lin", "时域处理", StepKind.TIME, ("增益",), "线性增益", (
        ParameterSpec("a0", "A0", "1.0"),
        ParameterSpec("k", "斜率k", "0.05"),
    )),
    OperationSpec("gain_exp", "时域处理", StepKind.TIME, ("增益",), "指数增益", (
        ParameterSpec("c", "常数C", "1.0"),
        ParameterSpec("p", "指数p", "1.0"),
    )),
    OperationSpec("gain_sph", "时域处理", StepKind.TIME, ("增益",), "球面扩散补偿", (ParameterSpec("v", "速度(m/ns)", "0.1"),)),
    OperationSpec("gain_sec", "时域处理", StepKind.TIME, ("增益",), "SEC 增益", (ParameterSpec("db", "强度 dB", "50"),)),
    OperationSpec("gain_agc", "时域处理", StepKind.TIME, ("增益",), "AGC 增益", (ParameterSpec("win", "窗口", "50", _int_cast),)),
    OperationSpec("gain_tgc_agc", "时域处理", StepKind.TIME, ("增益",), "TGC+AGC 混合增益", (ParameterSpec("alpha", "Alpha", "0.4"),)),
    OperationSpec("gain_bg_est", "时域处理", StepKind.TIME, ("增益",), "背景估计增益", (ParameterSpec("win", "平滑窗口", "30", _int_cast),)),
    OperationSpec("gain_ada_contrast", "时域处理", StepKind.TIME, ("增益",), "自适应对比度增强", (
        ParameterSpec("win_ns", "窗口大小(ns)", "10"),
        ParameterSpec("strength", "增强强度", "3"),
    )),
    OperationSpec("filt_vert", "时域处理", StepKind.TIME, ("滤波", "一维滤波"), "垂直滤波", (ParameterSpec("win", "窗口", "5", _int_cast),)),
    OperationSpec("filt_horz", "时域处理", StepKind.TIME, ("滤波", "一维滤波"), "水平滤波", (ParameterSpec("win", "窗口", "5", _int_cast),)),
    OperationSpec("filt_bp_fft", "时域处理", StepKind.TIME, ("滤波", "一维滤波"), "FFT 带通", (
        ParameterSpec("low", "低频(MHz)", "100"),
        ParameterSpec("high", "高频(MHz)", "800"),
    )),
    OperationSpec("filt_decon", "时域处理", StepKind.TIME, ("滤波", "一维滤波"), "预测反褶积", (
        ParameterSpec("len", "算子长度", "20", _int_cast),
        ParameterSpec("step", "预测步长", "5", _int_cast),
        ParameterSpec("start", "窗口起点", "0", _int_cast),
        ParameterSpec("end", "窗口终点", "0", _int_cast),
    )),
    OperationSpec("filt_med2", "时域处理", StepKind.TIME, ("滤波", "二维滤波"), "二维中值滤波", (ParameterSpec("win", "窗口", "3", _int_cast),)),
    OperationSpec("filt_mean2", "时域处理", StepKind.TIME, ("滤波", "二维滤波"), "二维均值滤波", (ParameterSpec("win", "窗口", "3", _int_cast),)),
    OperationSpec("filt_bp2d", "时域处理", StepKind.TIME, ("滤波", "二维滤波"), "二维 FFT 带通", (
        ParameterSpec("inner", "内径", "5"),
        ParameterSpec("outer", "外径", "50"),
    )),
    OperationSpec("filt_fk", "时域处理", StepKind.TIME, ("滤波", "二维滤波"), "FK Notch", (
        ParameterSpec("width", "切除宽度", "5", _int_cast),
        ParameterSpec("start", "起始时间(ns)", "1"),
    )),
    OperationSpec("filt_fk_shift", "时域处理", StepKind.TIME, ("滤波", "二维滤波"), "FK Shift", (
        ParameterSpec("width", "宽度", "5", _int_cast),
        ParameterSpec("shift", "偏移", "10"),
    )),
    OperationSpec("filt_glp", "时域处理", StepKind.TIME, ("滤波", "二维滤波"), "渐进低通", (
        ParameterSpec("mode", "模式(1/2/3)", "3", _int_cast),
        ParameterSpec("strength", "强度(1-4)", "2", _int_cast),
    )),
    OperationSpec("filt_smooth_fk", "时域处理", StepKind.TIME, ("滤波", "二维滤波"), "Smoothing FK", (ParameterSpec("strength", "强度(1-4)", "2", _int_cast),)),
    OperationSpec("mig_kirchhoff", "时域处理", StepKind.TIME, ("迁移/偏移",), "Kirchhoff 迁移", (
        ParameterSpec("v", "速度 v (m/ns)", "0.10"),
        ParameterSpec("dx", "道间距 dx (m)", "0.05"),
        ParameterSpec("r", "最大半径 Rmax (m)", "0.75"),
        ParameterSpec("a", "半角 (deg)", "30"),
    )),
    OperationSpec("mig_stolt", "时域处理", StepKind.TIME, ("迁移/偏移",), "Stolt 迁移", (
        ParameterSpec("v", "速度 v (m/ns)", "0.10"),
        ParameterSpec("dx", "道间距 dx (m)", "0.05"),
        ParameterSpec("ov", "X 重叠(Trace)", "32", _int_cast),
    )),
    OperationSpec("filt_grad", "时域处理", StepKind.TIME, ("滤波", "高级滤波"), "水平梯度"),
    OperationSpec("filt_wiener", "时域处理", StepKind.TIME, ("滤波", "高级滤波"), "Wiener", (ParameterSpec("win", "窗口", "3", _int_cast),)),
    OperationSpec("filt_notch", "时域处理", StepKind.TIME, ("滤波", "高级滤波"), "Notch", (ParameterSpec("freq", "频率(MHz)", "50"),)),
    OperationSpec("filt_adapt", "时域处理", StepKind.TIME, ("滤波", "高级滤波"), "自适应滤波"),
    OperationSpec("filt_hann_window", "频域处理", StepKind.FREQUENCY, ("滤波", "高级滤波"), "Hann Window"),
    OperationSpec("wav_swt1", "时域处理", StepKind.TIME, ("小波",), "1D SWT"),
    OperationSpec("wav_dwt2", "时域处理", StepKind.TIME, ("小波",), "2D DWT"),
    OperationSpec("freq_wavelet", "频域处理", StepKind.FREQUENCY, ("小波",), "频域小波", (ParameterSpec("level", "分解层数", "5", _int_cast),)),
    OperationSpec("cs_reconstruction", "时域处理", StepKind.TIME, ("小波",), "压缩感知重构", (
        ParameterSpec("sampling", "采样率(%)", "10"),
        ParameterSpec("lambda", "正则参数", "0.1"),
    )),
)

SPEC_BY_TYPE = {spec.op_type: spec for spec in OPERATION_SPECS}

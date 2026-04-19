from __future__ import annotations

from dataclasses import dataclass

from gpr_lab_pro.domain.enums import StepKind


@dataclass(frozen=True)
class ProcessingModuleSpec:
    module_id: str
    title: str
    kind: StepKind
    operation_types: tuple[str, ...]


MODULE_SPECS: tuple[ProcessingModuleSpec, ...] = (
    ProcessingModuleSpec(
        "time_frequency_transform",
        "时频域桥接",
        StepKind.TRANSFORM,
        ("ifft", "czt", "isdft"),
    ),
    ProcessingModuleSpec(
        "frequency_preprocess",
        "频域预处理",
        StepKind.FREQUENCY,
        ("phase_correction", "bg_freq", "filt_hann_window", "freq_wavelet"),
    ),
    ProcessingModuleSpec(
        "frequency_interference",
        "频域干扰抑制",
        StepKind.FREQUENCY,
        ("interf_active", "interf_passive"),
    ),
    ProcessingModuleSpec(
        "time_preprocess",
        "时域预处理",
        StepKind.TIME,
        ("dewow", "t0", "t0_fb"),
    ),
    ProcessingModuleSpec(
        "background_removal",
        "去背景",
        StepKind.TIME,
        (
            "bg_mean",
            "bg_median",
            "bg_move",
            "bg_svd",
            "bg_rpca",
            "bg_top_mute",
            "bg_highpass",
            "bg_adaptive",
        ),
    ),
    ProcessingModuleSpec(
        "gain_compensation",
        "增益补偿",
        StepKind.TIME,
        (
            "gain_lin",
            "gain_exp",
            "gain_sph",
            "gain_sec",
            "gain_agc",
            "gain_tgc_agc",
            "gain_bg_est",
            "gain_ada_contrast",
        ),
    ),
    ProcessingModuleSpec(
        "filter_1d",
        "一维滤波",
        StepKind.TIME,
        ("filt_vert", "filt_horz", "filt_bp_fft", "filt_decon"),
    ),
    ProcessingModuleSpec(
        "filter_2d",
        "二维滤波",
        StepKind.TIME,
        (
            "filt_med2",
            "filt_mean2",
            "filt_bp2d",
            "filt_fk",
            "filt_fk_shift",
            "filt_glp",
            "filt_smooth_fk",
        ),
    ),
    ProcessingModuleSpec(
        "filter_advanced",
        "高级滤波",
        StepKind.TIME,
        ("filt_grad", "filt_wiener", "filt_notch", "filt_adapt"),
    ),
    ProcessingModuleSpec(
        "migration",
        "迁移成像",
        StepKind.TIME,
        ("mig_kirchhoff", "mig_stolt"),
    ),
    ProcessingModuleSpec(
        "wavelet_reconstruction",
        "小波与重构",
        StepKind.TIME,
        ("wav_swt1", "wav_dwt2", "cs_reconstruction"),
    ),
)


MODULE_BY_OPERATION: dict[str, ProcessingModuleSpec] = {
    op_type: module for module in MODULE_SPECS for op_type in module.operation_types
}


def module_for_operation(op_type: str) -> ProcessingModuleSpec | None:
    return MODULE_BY_OPERATION.get(op_type) or MODULE_BY_OPERATION.get(op_type.lower())

#pragma once

#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32)
#  if defined(CIRCUITMIND_CORE_BUILD)
#    define CM_API __declspec(dllexport)
#  else
#    define CM_API __declspec(dllimport)
#  endif
#else
#  define CM_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* Stable C boundary v1. All electrical values passed here are normalized SI. */
typedef struct cm_element_v1 {
    uint32_t kind;       /* 0 resistor, 1 capacitor, 2 voltage source, 3 current source, 4 inductor, 5 PWL DC diode, 6 Shockley DC diode */
    uint32_t node_a;
    uint32_t node_b;
    double value;        /* ohm, farad, volt, or ampere according to kind */
    double initial_value;/* capacitor initial volts, diode off ohms, Shockley ideality, inductor initial amps, or source-step initial value */
    double final_value;  /* source-step final value or Shockley diode temperature K */
    double step_time_s;
    uint32_t has_step;   /* zero = DC value in value; one = ideal single step */
} cm_element_v1;

typedef struct cm_dc_request_v1 {
    uint32_t request_version; /* must be 1 */
    uint32_t node_count;      /* includes ground */
    uint32_t ground_index;
    uint32_t element_count;
    const cm_element_v1* elements;
} cm_dc_request_v1;

typedef struct cm_dc_result_v2 {
    double* node_voltages_v;
    size_t node_capacity;
    double* element_voltages_v;
    size_t element_voltage_capacity;
    double* element_currents_a;
    size_t element_current_capacity;
    double* element_powers_w;
    size_t element_power_capacity;
    double maximum_equation_residual;
    double maximum_kcl_residual_a;
    double maximum_voltage_source_residual_v;
    double normalized_equation_residual;
    double total_power_balance_w;
    double minimum_scaled_pivot;
    double maximum_scaled_pivot;
    size_t unknown_count;
} cm_dc_result_v2;

typedef struct cm_dc_sweep_request_v1 {
    uint32_t request_version; /* must be 1 */
    cm_dc_request_v1 dc_request;
    uint32_t swept_element_index;
    size_t point_count;
    const double* source_values; /* strictly increasing SI voltage/current values */
} cm_dc_sweep_request_v1;

typedef struct cm_dc_sweep_result_v1 {
    double* node_voltage_traces_v;
    size_t node_voltage_capacity;
    double* maximum_equation_residuals;
    size_t maximum_residual_capacity;
    double* normalized_equation_residuals;
    size_t normalized_residual_capacity;
    size_t point_count;
    size_t node_count;
    size_t unknown_count;
} cm_dc_sweep_result_v1;

typedef struct cm_transient_request_v1 {
    uint32_t request_version; /* must be 1 */
    uint32_t node_count;      /* includes ground */
    uint32_t ground_index;
    uint32_t element_count;
    const cm_element_v1* elements;
    double time_step_s;
    double stop_time_s;
    uint32_t maximum_steps;
} cm_transient_request_v1;

typedef struct cm_ac_element_v1 {
    uint32_t kind;       /* 0 resistor, 1 capacitor, 2 voltage source, 3 current source, 4 inductor */
    uint32_t node_a;
    uint32_t node_b;
    double value;        /* ohm, farad, henry, or AC RMS magnitude in V/A */
    double phase_rad;    /* source phase; must be zero for passive elements */
} cm_ac_element_v1;

typedef struct cm_ac_sweep_request_v1 {
    uint32_t request_version; /* must be 1 */
    uint32_t node_count;      /* includes ground */
    uint32_t ground_index;
    uint32_t element_count;
    const cm_ac_element_v1* elements;
    size_t frequency_count;
    const double* frequencies_hz; /* strictly increasing positive frequencies */
} cm_ac_sweep_request_v1;

typedef struct cm_ac_sweep_result_v1 {
    double* node_voltage_real_v;
    size_t node_voltage_real_capacity;
    double* node_voltage_imag_v;
    size_t node_voltage_imag_capacity;
    double* normalized_equation_residuals;
    size_t residual_capacity;
    size_t point_count;
    size_t node_count;
    size_t unknown_count;
} cm_ac_sweep_result_v1;

typedef struct cm_digital_gate_v1 {
    uint32_t kind; /* 0 NOT, 1 buffer, 2 AND, 3 OR, 4 XOR, 5 NAND, 6 NOR, 7 XNOR */
    uint32_t input_a;
    uint32_t input_b; /* ignored for NOT and buffer */
    uint32_t output;
} cm_digital_gate_v1;

typedef struct cm_digital_waveform_gate_v1 {
    uint32_t kind; /* 0 NOT, 1 buffer, 2 AND, 3 OR, 4 XOR, 5 NAND, 6 NOR, 7 XNOR, 8 D flip-flop */
    uint32_t input_a; /* D input for DFF */
    uint32_t input_b; /* ignored for NOT and buffer; clock input for DFF */
    uint32_t output;
    uint8_t initial_value; /* DFF initial Q state; ignored for combinational gates */
    uint8_t active_edge; /* DFF: 0 rising (default), 1 falling; ignored for combinational gates */
} cm_digital_waveform_gate_v1;

typedef struct cm_digital_truth_table_request_v1 {
    uint32_t request_version; /* must be 1 */
    uint32_t net_count;
    uint32_t gate_count;
    const cm_digital_gate_v1* gates;
    uint32_t input_count;
    const uint32_t* input_nets;
    uint32_t output_count;
    const uint32_t* output_nets;
} cm_digital_truth_table_request_v1;

typedef struct cm_digital_truth_table_result_v1 {
    uint8_t* output_values; /* row-major 0/1 bytes */
    size_t output_capacity;
    size_t row_count;
    size_t output_count;
} cm_digital_truth_table_result_v1;

typedef struct cm_digital_waveform_request_v1 {
    uint32_t request_version; /* must be 1 */
    uint32_t net_count;
    uint32_t gate_count;
    const cm_digital_waveform_gate_v1* gates;
    uint32_t input_count;
    const uint32_t* input_nets;
    uint32_t output_count;
    const uint32_t* output_nets;
    size_t sample_count;
    const uint8_t* primary_input_samples; /* row-major sample * input_count; values must be 0 or 1 */
    double sample_interval_s;
} cm_digital_waveform_request_v1;

typedef struct cm_digital_waveform_result_v1 {
    double* sample_times_s;
    size_t sample_time_capacity;
    uint8_t* output_values; /* row-major sample * output_count */
    size_t output_capacity;
    size_t sample_count;
    size_t output_count;
} cm_digital_waveform_result_v1;

CM_API const char* cm_engine_version(void);
CM_API uint32_t cm_engine_abi_version(void);
CM_API int cm_engine_solve_dc_v1(const cm_dc_request_v1* request,
                                  double* node_voltages_v, size_t node_capacity,
                                  double* maximum_equation_residual,
                                  char* error_message, size_t error_capacity);
CM_API int cm_engine_solve_dc_v2(const cm_dc_request_v1* request,
                                  cm_dc_result_v2* result,
                                  char* error_message, size_t error_capacity);
CM_API int cm_engine_simulate_dc_sweep_v1(const cm_dc_sweep_request_v1* request,
                                          cm_dc_sweep_result_v1* result,
                                          char* error_message, size_t error_capacity);
CM_API int cm_engine_simulate_transient_rc_v1(const cm_transient_request_v1* request,
                                               double* time_points_s, size_t time_capacity,
                                               double* node_voltages_v, size_t voltage_capacity,
                                               size_t* written_samples,
                                               double* maximum_equation_residual,
                                               char* error_message, size_t error_capacity);
CM_API int cm_engine_simulate_ac_sweep_v1(const cm_ac_sweep_request_v1* request,
                                          cm_ac_sweep_result_v1* result,
                                          char* error_message, size_t error_capacity);
CM_API int cm_engine_simulate_digital_truth_table_v1(
    const cm_digital_truth_table_request_v1* request,
    cm_digital_truth_table_result_v1* result,
    char* error_message, size_t error_capacity);
CM_API int cm_engine_simulate_digital_waveform_v1(
    const cm_digital_waveform_request_v1* request,
    cm_digital_waveform_result_v1* result,
    char* error_message, size_t error_capacity);

#ifdef __cplusplus
}
#endif

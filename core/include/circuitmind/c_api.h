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
    uint32_t kind;       /* 0 resistor, 1 capacitor, 2 voltage source, 3 current source */
    uint32_t node_a;
    uint32_t node_b;
    double value;        /* ohm, farad, volt, or ampere according to kind */
    double initial_value;/* capacitor initial volts or source-step initial value */
    double final_value;  /* source-step final value */
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

CM_API const char* cm_engine_version(void);
CM_API uint32_t cm_engine_abi_version(void);
CM_API int cm_engine_solve_dc_v1(const cm_dc_request_v1* request,
                                  double* node_voltages_v, size_t node_capacity,
                                  double* maximum_equation_residual,
                                  char* error_message, size_t error_capacity);
CM_API int cm_engine_solve_dc_v2(const cm_dc_request_v1* request,
                                  cm_dc_result_v2* result,
                                  char* error_message, size_t error_capacity);
CM_API int cm_engine_simulate_transient_rc_v1(const cm_transient_request_v1* request,
                                               double* time_points_s, size_t time_capacity,
                                               double* node_voltages_v, size_t voltage_capacity,
                                               size_t* written_samples,
                                               double* maximum_equation_residual,
                                               char* error_message, size_t error_capacity);

#ifdef __cplusplus
}
#endif

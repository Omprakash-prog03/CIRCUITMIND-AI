#include "circuitmind/c_api.h"
#include "circuitmind/engine.hpp"

#include <algorithm>
#include <cstring>
#include <exception>
#include <limits>
#include <string>
#include <vector>

namespace {
void setError(char* buffer, size_t capacity, const char* message) noexcept {
    if (buffer == nullptr || capacity == 0) return;
    const auto length = std::min(capacity - 1, std::strlen(message));
    std::memcpy(buffer, message, length);
    buffer[length] = '\0';
}

circuitmind::Circuit convertCircuit(uint32_t node_count, uint32_t ground_index,
                                    uint32_t element_count, const cm_element_v1* elements) {
    if (node_count > 151 || element_count > 500) throw circuitmind::SolverError("C ABI input exceeds the 151-node or 500-element limit");
    if (element_count != 0 && elements == nullptr) throw circuitmind::SolverError("elements pointer must not be null");
    circuitmind::Circuit circuit;
    circuit.node_count = node_count;
    circuit.ground_index = ground_index;
    circuit.elements.reserve(element_count);
    for (uint32_t index = 0; index < element_count; ++index) {
        const auto& input = elements[index];
        if (input.kind > static_cast<uint32_t>(circuitmind::ElementKind::CurrentSource)) {
            throw circuitmind::SolverError("element has unsupported kind code");
        }
        if (input.has_step > 1) throw circuitmind::SolverError("element has invalid has_step flag");
        circuit.elements.push_back({static_cast<circuitmind::ElementKind>(input.kind), input.node_a, input.node_b,
                                    input.value, input.initial_value, input.final_value, input.step_time_s,
                                    input.has_step != 0});
    }
    return circuit;
}

} // namespace

extern "C" {
const char* cm_engine_version(void) {
    return circuitmind::engineVersion();
}

uint32_t cm_engine_abi_version(void) {
    return 1;
}

int cm_engine_solve_dc_v1(const cm_dc_request_v1* request,
                          double* node_voltages_v, size_t node_capacity,
                          double* maximum_equation_residual,
                          char* error_message, size_t error_capacity) {
    try {
        if (request == nullptr) throw circuitmind::SolverError("request pointer must not be null");
        if (request->request_version != 1) throw circuitmind::SolverError("only DC request_version 1 is supported");
        if (node_voltages_v == nullptr || node_capacity < request->node_count) {
            throw circuitmind::SolverError("node-voltage output buffer is null or too small");
        }
        if (maximum_equation_residual == nullptr) throw circuitmind::SolverError("residual output pointer must not be null");
        const auto circuit = convertCircuit(request->node_count, request->ground_index,
                                            request->element_count, request->elements);
        const auto result = circuitmind::solveDc(circuit);
        std::copy(result.node_voltages_v.begin(), result.node_voltages_v.end(), node_voltages_v);
        *maximum_equation_residual = result.maximum_equation_residual;
        setError(error_message, error_capacity, "");
        return 0;
    } catch (const std::exception& error) {
        setError(error_message, error_capacity, error.what());
        return 1;
    } catch (...) {
        setError(error_message, error_capacity, "unknown C++ engine failure");
        return 2;
    }
}

int cm_engine_solve_dc_v2(const cm_dc_request_v1* request, cm_dc_result_v2* output,
                          char* error_message, size_t error_capacity) {
    try {
        if (request == nullptr) throw circuitmind::SolverError("request pointer must not be null");
        if (output == nullptr) throw circuitmind::SolverError("DC result pointer must not be null");
        if (request->request_version != 1) throw circuitmind::SolverError("only DC request_version 1 is supported");
        if (output->node_voltages_v == nullptr || output->node_capacity < request->node_count ||
            output->element_voltages_v == nullptr || output->element_voltage_capacity < request->element_count ||
            output->element_currents_a == nullptr || output->element_current_capacity < request->element_count ||
            output->element_powers_w == nullptr || output->element_power_capacity < request->element_count) {
            throw circuitmind::SolverError("DC result buffers are null or too small");
        }
        const auto circuit = convertCircuit(request->node_count, request->ground_index,
                                            request->element_count, request->elements);
        const auto solved = circuitmind::solveDc(circuit);
        std::copy(solved.node_voltages_v.begin(), solved.node_voltages_v.end(), output->node_voltages_v);
        std::copy(solved.element_voltages_v.begin(), solved.element_voltages_v.end(), output->element_voltages_v);
        std::copy(solved.element_currents_a.begin(), solved.element_currents_a.end(), output->element_currents_a);
        std::copy(solved.element_powers_w.begin(), solved.element_powers_w.end(), output->element_powers_w);
        output->maximum_equation_residual = solved.maximum_equation_residual;
        output->maximum_kcl_residual_a = solved.maximum_kcl_residual_a;
        output->maximum_voltage_source_residual_v = solved.maximum_voltage_source_residual_v;
        output->normalized_equation_residual = solved.normalized_equation_residual;
        output->total_power_balance_w = solved.total_power_balance_w;
        output->minimum_scaled_pivot = solved.minimum_scaled_pivot;
        output->maximum_scaled_pivot = solved.maximum_scaled_pivot;
        output->unknown_count = solved.unknown_count;
        setError(error_message, error_capacity, "");
        return 0;
    } catch (const std::exception& error) {
        setError(error_message, error_capacity, error.what());
        return 1;
    } catch (...) {
        setError(error_message, error_capacity, "unknown C++ engine failure");
        return 2;
    }
}

int cm_engine_simulate_transient_rc_v1(const cm_transient_request_v1* request,
                                       double* time_points_s, size_t time_capacity,
                                       double* node_voltages_v, size_t voltage_capacity,
                                       size_t* written_samples,
                                       double* maximum_equation_residual,
                                       char* error_message, size_t error_capacity) {
    try {
        if (request == nullptr) throw circuitmind::SolverError("request pointer must not be null");
        if (request->request_version != 1) throw circuitmind::SolverError("only transient request_version 1 is supported");
        if (written_samples == nullptr || maximum_equation_residual == nullptr) {
            throw circuitmind::SolverError("output metadata pointers must not be null");
        }
        const auto circuit = convertCircuit(request->node_count, request->ground_index,
                                            request->element_count, request->elements);
        const auto result = circuitmind::simulateTransientRc(circuit,
            {request->time_step_s, request->stop_time_s, request->maximum_steps});
        const auto expected_voltage_count = result.time_points_s.size() * result.node_count;
        if (time_points_s == nullptr || time_capacity < result.time_points_s.size() ||
            node_voltages_v == nullptr || voltage_capacity < expected_voltage_count) {
            throw circuitmind::SolverError("transient output buffers are null or too small");
        }
        std::copy(result.time_points_s.begin(), result.time_points_s.end(), time_points_s);
        std::copy(result.node_voltages_v.begin(), result.node_voltages_v.end(), node_voltages_v);
        *written_samples = result.time_points_s.size();
        *maximum_equation_residual = result.maximum_equation_residual;
        setError(error_message, error_capacity, "");
        return 0;
    } catch (const std::exception& error) {
        setError(error_message, error_capacity, error.what());
        return 1;
    } catch (...) {
        setError(error_message, error_capacity, "unknown C++ engine failure");
        return 2;
    }
}
}

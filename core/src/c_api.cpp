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
        if (input.kind > static_cast<uint32_t>(circuitmind::ElementKind::ShockleyDiode)) {
            throw circuitmind::SolverError("element has unsupported kind code");
        }
        if (input.has_step > 1) throw circuitmind::SolverError("element has invalid has_step flag");
        circuit.elements.push_back({static_cast<circuitmind::ElementKind>(input.kind), input.node_a, input.node_b,
                                    input.value, input.initial_value, input.final_value, input.step_time_s,
                                    input.has_step != 0});
    }
    return circuit;
}

circuitmind::AcCircuit convertAcCircuit(uint32_t node_count, uint32_t ground_index,
                                        uint32_t element_count, const cm_ac_element_v1* elements) {
    if (node_count > 65 || element_count > 200) throw circuitmind::SolverError("C ABI AC input exceeds the 65-node or 200-element limit");
    if (element_count != 0 && elements == nullptr) throw circuitmind::SolverError("AC elements pointer must not be null");
    circuitmind::AcCircuit circuit;
    circuit.node_count = node_count;
    circuit.ground_index = ground_index;
    circuit.elements.reserve(element_count);
    for (uint32_t index = 0; index < element_count; ++index) {
        const auto& input = elements[index];
        if (input.kind > static_cast<uint32_t>(circuitmind::ElementKind::Inductor)) {
            throw circuitmind::SolverError("AC element has unsupported kind code");
        }
        circuit.elements.push_back({static_cast<circuitmind::ElementKind>(input.kind), input.node_a, input.node_b,
                                    input.value, input.phase_rad});
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

int cm_engine_simulate_dc_sweep_v1(const cm_dc_sweep_request_v1* request,
                                   cm_dc_sweep_result_v1* output,
                                   char* error_message, size_t error_capacity) {
    try {
        if (request == nullptr) throw circuitmind::SolverError("DC sweep request pointer must not be null");
        if (output == nullptr) throw circuitmind::SolverError("DC sweep result pointer must not be null");
        if (request->request_version != 1 || request->dc_request.request_version != 1) {
            throw circuitmind::SolverError("only DC sweep and nested DC request_version 1 are supported");
        }
        if (request->point_count < 2 || request->point_count > 200 || request->source_values == nullptr) {
            throw circuitmind::SolverError("DC sweep requires 2-200 points and a non-null source-values array");
        }
        const auto circuit = convertCircuit(request->dc_request.node_count, request->dc_request.ground_index,
                                            request->dc_request.element_count, request->dc_request.elements);
        const auto solved = circuitmind::simulateDcSweep(circuit, request->swept_element_index,
            std::vector<double>(request->source_values, request->source_values + request->point_count));
        const size_t expected_values = solved.node_voltage_traces_v.size();
        if (output->node_voltage_traces_v == nullptr || output->node_voltage_capacity < expected_values ||
            output->maximum_equation_residuals == nullptr || output->maximum_residual_capacity < request->point_count ||
            output->normalized_equation_residuals == nullptr ||
            output->normalized_residual_capacity < request->point_count) {
            throw circuitmind::SolverError("DC sweep result buffers are null or too small");
        }
        std::copy(solved.node_voltage_traces_v.begin(), solved.node_voltage_traces_v.end(), output->node_voltage_traces_v);
        std::copy(solved.maximum_equation_residuals.begin(), solved.maximum_equation_residuals.end(), output->maximum_equation_residuals);
        std::copy(solved.normalized_equation_residuals.begin(), solved.normalized_equation_residuals.end(), output->normalized_equation_residuals);
        output->point_count = request->point_count;
        output->node_count = solved.node_count;
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

int cm_engine_simulate_ac_sweep_v1(const cm_ac_sweep_request_v1* request,
                                   cm_ac_sweep_result_v1* output,
                                   char* error_message, size_t error_capacity) {
    try {
        if (request == nullptr) throw circuitmind::SolverError("AC sweep request pointer must not be null");
        if (output == nullptr) throw circuitmind::SolverError("AC sweep result pointer must not be null");
        if (request->request_version != 1) throw circuitmind::SolverError("only AC request_version 1 is supported");
        if (request->node_count > 65 || request->element_count > 200 ||
            request->frequency_count == 0 || request->frequency_count > 200) {
            throw circuitmind::SolverError("AC request exceeds 65 nodes, 200 elements, or 200 frequency points");
        }
        const size_t expected_values = request->frequency_count * request->node_count;
        if (request->frequencies_hz == nullptr || output->node_voltage_real_v == nullptr ||
            output->node_voltage_real_capacity < expected_values || output->node_voltage_imag_v == nullptr ||
            output->node_voltage_imag_capacity < expected_values || output->normalized_equation_residuals == nullptr ||
            output->residual_capacity < request->frequency_count) {
            throw circuitmind::SolverError("AC result buffers are null or too small");
        }
        const auto circuit = convertAcCircuit(request->node_count, request->ground_index,
                                              request->element_count, request->elements);
        const std::vector<double> frequencies(request->frequencies_hz,
                                              request->frequencies_hz + request->frequency_count);
        const auto solved = circuitmind::simulateAcSweep(circuit, frequencies);
        std::copy(solved.node_voltage_real_v.begin(), solved.node_voltage_real_v.end(), output->node_voltage_real_v);
        std::copy(solved.node_voltage_imag_v.begin(), solved.node_voltage_imag_v.end(), output->node_voltage_imag_v);
        std::copy(solved.normalized_equation_residuals.begin(), solved.normalized_equation_residuals.end(), output->normalized_equation_residuals);
        output->point_count = solved.frequency_points_hz.size();
        output->node_count = solved.node_count;
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

int cm_engine_simulate_digital_truth_table_v1(
    const cm_digital_truth_table_request_v1* request,
    cm_digital_truth_table_result_v1* output,
    char* error_message, size_t error_capacity) {
    try {
        if (request == nullptr) throw circuitmind::SolverError("digital truth-table request pointer must not be null");
        if (output == nullptr) throw circuitmind::SolverError("digital truth-table result pointer must not be null");
        if (request->request_version != 1) throw circuitmind::SolverError("only digital request_version 1 is supported");
        if (request->net_count > 256 || request->gate_count > 512 ||
            request->input_count > 10 || request->output_count > 32) {
            throw circuitmind::SolverError("digital truth-table request exceeds configured net, gate, input, or output limits");
        }
        if (request->gates == nullptr || request->input_nets == nullptr || request->output_nets == nullptr) {
            throw circuitmind::SolverError("digital truth-table gate and net-index arrays must not be null");
        }
        const size_t row_count = size_t{1} << request->input_count;
        const size_t expected_values = row_count * request->output_count;
        if (output->output_values == nullptr || output->output_capacity < expected_values) {
            throw circuitmind::SolverError("digital truth-table output buffer is null or too small");
        }
        circuitmind::DigitalCircuit circuit;
        circuit.net_count = request->net_count;
        circuit.input_nets.assign(request->input_nets, request->input_nets + request->input_count);
        circuit.output_nets.assign(request->output_nets, request->output_nets + request->output_count);
        circuit.gates.reserve(request->gate_count);
        for (uint32_t index = 0; index < request->gate_count; ++index) {
            const auto& gate = request->gates[index];
            if (gate.kind > static_cast<uint32_t>(circuitmind::DigitalGateKind::Xnor)) {
                throw circuitmind::SolverError("digital gate has an unsupported kind code");
            }
            circuit.gates.push_back({static_cast<circuitmind::DigitalGateKind>(gate.kind),
                                     gate.input_a, gate.input_b, gate.output, 0});
        }
        const auto solved = circuitmind::simulateDigitalTruthTable(circuit);
        std::copy(solved.output_values.begin(), solved.output_values.end(), output->output_values);
        output->row_count = solved.row_count;
        output->output_count = solved.output_count;
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

int cm_engine_simulate_digital_waveform_v1(
    const cm_digital_waveform_request_v1* request,
    cm_digital_waveform_result_v1* output,
    char* error_message, size_t error_capacity) {
    try {
        if (request == nullptr) throw circuitmind::SolverError("digital waveform request pointer must not be null");
        if (output == nullptr) throw circuitmind::SolverError("digital waveform result pointer must not be null");
        if (request->request_version != 1) throw circuitmind::SolverError("only digital waveform request_version 1 is supported");
        if (request->net_count > 256 || request->gate_count > 512 || request->input_count > 32 ||
            request->output_count > 32 || request->sample_count == 0 || request->sample_count > 10000) {
            throw circuitmind::SolverError("digital waveform request exceeds configured size limits");
        }
        if (request->gates == nullptr || request->input_nets == nullptr || request->output_nets == nullptr ||
            request->primary_input_samples == nullptr || output->sample_times_s == nullptr ||
            output->output_values == nullptr) {
            throw circuitmind::SolverError("digital waveform input/output buffers must not be null");
        }
        const size_t input_value_count = request->sample_count * request->input_count;
        const size_t output_value_count = request->sample_count * request->output_count;
        if (output->sample_time_capacity < request->sample_count || output->output_capacity < output_value_count) {
            throw circuitmind::SolverError("digital waveform result buffers are too small");
        }
        circuitmind::DigitalCircuit circuit;
        circuit.net_count = request->net_count;
        circuit.input_nets.assign(request->input_nets, request->input_nets + request->input_count);
        circuit.output_nets.assign(request->output_nets, request->output_nets + request->output_count);
        circuit.gates.reserve(request->gate_count);
        for (uint32_t index = 0; index < request->gate_count; ++index) {
            const auto& gate = request->gates[index];
            if (gate.kind > static_cast<uint32_t>(circuitmind::DigitalGateKind::DFlipFlop)) {
                throw circuitmind::SolverError("digital gate has an unsupported kind code");
            }
            circuit.gates.push_back({static_cast<circuitmind::DigitalGateKind>(gate.kind), gate.input_a,
                                     gate.input_b, gate.output, gate.initial_value,
                                     static_cast<circuitmind::DigitalClockEdge>(gate.active_edge)});
        }
        const std::vector<std::uint8_t> samples(request->primary_input_samples,
                                                 request->primary_input_samples + input_value_count);
        const auto solved = circuitmind::simulateDigitalWaveform(circuit, samples,
                                                                 request->sample_count,
                                                                 request->sample_interval_s);
        std::copy(solved.sample_times_s.begin(), solved.sample_times_s.end(), output->sample_times_s);
        std::copy(solved.output_values.begin(), solved.output_values.end(), output->output_values);
        output->sample_count = solved.sample_times_s.size();
        output->output_count = solved.output_count;
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

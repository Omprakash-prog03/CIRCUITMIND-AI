#include "circuitmind/engine.hpp"
#include "circuitmind/c_api.h"

#ifdef NDEBUG
#undef NDEBUG
#endif
#include <cassert>
#include <cmath>
#include <iostream>
#include <stdexcept>
#include <string_view>

using circuitmind::AcCircuit;
using circuitmind::Circuit;
using circuitmind::Element;
using circuitmind::ElementKind;

void testVoltageDividerDc() {
    Circuit circuit{3, 0, {
        {ElementKind::VoltageSource, 1, 0, 10.0},
        {ElementKind::Resistor, 1, 2, 1000.0},
        {ElementKind::Resistor, 2, 0, 1000.0},
    }};
    const auto result = circuitmind::solveDc(circuit);
    assert(std::abs(result.node_voltages_v[1] - 10.0) < 1e-12);
    assert(std::abs(result.node_voltages_v[2] - 5.0) < 1e-12);
    assert(result.maximum_equation_residual < 1e-10);
}

void testShockleyDiodeNewtonOperatingPoint() {
    Circuit circuit{3, 0, {
        {ElementKind::VoltageSource, 1, 0, 5.0},
        {ElementKind::Resistor, 1, 2, 1000.0},
        {ElementKind::ShockleyDiode, 2, 0, 1e-12, 1.0, 300.0},
    }};
    const auto result = circuitmind::solveDc(circuit);
    const double diode_voltage = result.node_voltages_v[2];
    const double diode_current = result.element_currents_a[2];
    assert(diode_voltage > 0.5 && diode_voltage < 0.7);
    assert(diode_current > 0.0);
    assert(std::abs((5.0 - diode_voltage) / 1000.0 - diode_current) < 1e-8);
    assert(result.maximum_kcl_residual_a < 1e-8);
    assert(std::abs(result.total_power_balance_w) < 1e-8);
}

void testPiecewiseLinearDiodeOperatingPoints() {
    Circuit forward{3, 0, {
        {ElementKind::VoltageSource, 1, 0, 5.0},
        {ElementKind::Resistor, 1, 2, 1000.0},
        {ElementKind::Diode, 2, 0, 0.7, 1e12},
    }};
    const auto forward_result = circuitmind::solveDc(forward);
    assert(std::abs(forward_result.node_voltages_v[2] - 0.7) < 1e-10);
    assert(std::abs(forward_result.element_currents_a[2] - 4.3e-3) < 1e-9);
    assert(std::abs(forward_result.total_power_balance_w) < 1e-9);
    assert(forward_result.maximum_equation_residual < 1e-9);

    Circuit reverse{3, 0, {
        {ElementKind::VoltageSource, 1, 0, -5.0},
        {ElementKind::Resistor, 1, 2, 1000.0},
        {ElementKind::Diode, 2, 0, 0.7, 1e12},
    }};
    const auto reverse_result = circuitmind::solveDc(reverse);
    assert(std::abs(reverse_result.node_voltages_v[2] + 5.0) < 1e-8);
    assert(reverse_result.element_currents_a[2] < 0.0);
    assert(std::abs(reverse_result.element_currents_a[2] + 5e-12) < 1e-18);
}

void testIndependentCurrentSourceDirection() {
    Circuit circuit{2, 0, {
        {ElementKind::CurrentSource, 0, 1, 1e-3},
        {ElementKind::Resistor, 1, 0, 1000.0},
    }};
    const auto result = circuitmind::solveDc(circuit);
    assert(std::abs(result.node_voltages_v[1] - 1.0) < 1e-12);
}

void testBackwardEulerRcStep() {
    Circuit circuit{3, 0, {
        {ElementKind::VoltageSource, 1, 0, 0.0, 0.0, 5.0, 0.0, true},
        {ElementKind::Resistor, 1, 2, 1000.0},
        {ElementKind::Capacitor, 2, 0, 1e-6, 0.0},
    }};
    const auto result = circuitmind::simulateTransientRc(circuit, {100e-6, 1e-3, 2000});
    const double expected = 5.0 * (1.0 - std::pow(1.1, -10.0));
    assert(result.time_points_s.size() == 11);
    assert(std::abs(result.node_voltages_v[10 * 3 + 2] - expected) < 1e-11);
    assert(std::abs(result.node_voltages_v[0 * 3 + 1]) < 1e-12); // t=0- source
    assert(result.maximum_equation_residual < 1e-9);
}

void testRejectedSingularAndUnboundedRequests() {
    bool singular_rejected = false;
    try {
        Circuit singular{3, 0, {{ElementKind::Resistor, 0, 1, 1000.0}}};
        (void)circuitmind::solveDc(singular);
    } catch (const circuitmind::SolverError&) { singular_rejected = true; }
    assert(singular_rejected);

    bool waveform_rejected_by_dc = false;
    try {
        Circuit waveform{2, 0, {{ElementKind::VoltageSource, 1, 0, 0.0, 0.0, 1.0, 0.0, true},
                                {ElementKind::Resistor, 1, 0, 1000.0}}};
        (void)circuitmind::solveDc(waveform);
    } catch (const circuitmind::SolverError&) { waveform_rejected_by_dc = true; }
    assert(waveform_rejected_by_dc);

    bool step_limit_rejected = false;
    try {
        Circuit circuit{2, 0, {{ElementKind::VoltageSource, 1, 0, 0.0, 0.0, 1.0, 0.0, true},
                               {ElementKind::Capacitor, 1, 0, 1e-6, 0.0}}};
        (void)circuitmind::simulateTransientRc(circuit, {1e-6, 2001e-6, 2000});
    } catch (const circuitmind::SolverError&) { step_limit_rejected = true; }
    assert(step_limit_rejected);

    bool inductor_rejected_by_dc = false;
    try {
        Circuit circuit{2, 0, {{ElementKind::Inductor, 1, 0, 1.0}}};
        (void)circuitmind::solveDc(circuit);
    } catch (const circuitmind::SolverError&) { inductor_rejected_by_dc = true; }
    assert(inductor_rejected_by_dc);

    bool shockley_parameter_rejected = false;
    try {
        Circuit circuit{2, 0, {{ElementKind::Resistor, 1, 0, 1000.0},
                               {ElementKind::ShockleyDiode, 1, 0, 1e-2, 1.0, 300.0}}};
        (void)circuitmind::solveDc(circuit);
    } catch (const circuitmind::SolverError&) { shockley_parameter_rejected = true; }
    assert(shockley_parameter_rejected);

    bool mixed_diode_models_rejected = false;
    try {
        Circuit circuit{2, 0, {{ElementKind::Resistor, 1, 0, 1000.0},
                               {ElementKind::Diode, 1, 0, 0.7, 1e12},
                               {ElementKind::ShockleyDiode, 1, 0, 1e-12, 1.0, 300.0}}};
        (void)circuitmind::solveDc(circuit);
    } catch (const circuitmind::SolverError&) { mixed_diode_models_rejected = true; }
    assert(mixed_diode_models_rejected);

    bool shockley_rejected_by_transient = false;
    try {
        Circuit circuit{2, 0, {{ElementKind::Capacitor, 1, 0, 1e-6, 0.0},
                               {ElementKind::Resistor, 1, 0, 1000.0},
                               {ElementKind::ShockleyDiode, 1, 0, 1e-12, 1.0, 300.0}}};
        (void)circuitmind::simulateTransientRc(circuit, {1e-6, 2e-6, 10});
    } catch (const circuitmind::SolverError&) { shockley_rejected_by_transient = true; }
    assert(shockley_rejected_by_transient);
}

void testAcSweepAndCAbi() {
    constexpr double pi = 3.14159265358979323846;
    const AcCircuit circuit{3, 0, {
        {ElementKind::VoltageSource, 1, 0, 1.0, 0.0},
        {ElementKind::Resistor, 1, 2, 1000.0, 0.0},
        {ElementKind::Capacitor, 2, 0, 1.0 / (2.0 * pi * 1000.0 * 1000.0), 0.0},
    }};
    const auto result = circuitmind::simulateAcSweep(circuit, {100.0, 1000.0, 10000.0});
    assert(result.frequency_points_hz.size() == 3);
    assert(std::abs(result.node_voltage_real_v[1 * 3 + 2] - 0.5) < 1e-10);
    assert(std::abs(result.node_voltage_imag_v[1 * 3 + 2] + 0.5) < 1e-10);
    assert(result.normalized_equation_residuals[1] < 1e-12);

    const AcCircuit rl_lowpass{3, 0, {
        {ElementKind::VoltageSource, 1, 0, 1.0, 0.0},
        {ElementKind::Inductor, 1, 2, 1000.0 / (2.0 * pi * 1000.0), 0.0},
        {ElementKind::Resistor, 2, 0, 1000.0, 0.0},
    }};
    const auto rl_result = circuitmind::simulateAcSweep(rl_lowpass, {100.0, 1000.0, 10000.0});
    assert(std::abs(rl_result.node_voltage_real_v[5] - 0.5) < 1e-10);
    assert(std::abs(rl_result.node_voltage_imag_v[5] + 0.5) < 1e-10);

    bool duplicate_frequency_rejected = false;
    try { (void)circuitmind::simulateAcSweep(circuit, {100.0, 100.0}); }
    catch (const circuitmind::SolverError&) { duplicate_frequency_rejected = true; }
    assert(duplicate_frequency_rejected);

    const cm_ac_element_v1 elements[] = {
        {static_cast<uint32_t>(ElementKind::VoltageSource), 1, 0, 1.0, 0.0},
        {static_cast<uint32_t>(ElementKind::Resistor), 1, 2, 1000.0, 0.0},
        {static_cast<uint32_t>(ElementKind::Capacitor), 2, 0, 1.0 / (2.0 * pi * 1000.0 * 1000.0), 0.0},
    };
    const double frequencies[] = {100.0, 1000.0, 10000.0};
    const cm_ac_sweep_request_v1 request{1, 3, 0, 3, elements, 3, frequencies};
    double real[9]{}, imag[9]{}, residual[3]{};
    cm_ac_sweep_result_v1 output{};
    output.node_voltage_real_v = real; output.node_voltage_real_capacity = 9;
    output.node_voltage_imag_v = imag; output.node_voltage_imag_capacity = 9;
    output.normalized_equation_residuals = residual; output.residual_capacity = 3;
    char error[256]{};
    const int status = cm_engine_simulate_ac_sweep_v1(&request, &output, error, sizeof(error));
    assert(status == 0);
    assert(output.point_count == 3 && output.node_count == 3 && output.unknown_count == 3);
    assert(std::abs(real[5] - 0.5) < 1e-10);
    assert(std::abs(imag[5] + 0.5) < 1e-10);
    output.node_voltage_real_capacity = 1;
    const int short_buffer_status = cm_engine_simulate_ac_sweep_v1(&request, &output, error, sizeof(error));
    assert(short_buffer_status != 0);
    assert(std::string_view(error).find("too small") != std::string_view::npos);

    const cm_ac_element_v1 rl_elements[] = {
        {static_cast<uint32_t>(ElementKind::VoltageSource), 1, 0, 1.0, 0.0},
        {static_cast<uint32_t>(ElementKind::Inductor), 1, 2, 1000.0 / (2.0 * pi * 1000.0), 0.0},
        {static_cast<uint32_t>(ElementKind::Resistor), 2, 0, 1000.0, 0.0},
    };
    const cm_ac_sweep_request_v1 rl_request{1, 3, 0, 3, rl_elements, 3, frequencies};
    output.node_voltage_real_capacity = 9;
    const int rl_status = cm_engine_simulate_ac_sweep_v1(&rl_request, &output, error, sizeof(error));
    assert(rl_status == 0);
    assert(std::abs(real[5] - 0.5) < 1e-10);
    assert(std::abs(imag[5] + 0.5) < 1e-10);
}

void testBackwardEulerRlStep() {
    Circuit circuit{3, 0, {
        {ElementKind::VoltageSource, 1, 0, 0.0, 0.0, 1.0, 0.1, true},
        {ElementKind::Resistor, 1, 2, 1.0},
        {ElementKind::Inductor, 2, 0, 1.0, 0.0},
    }};
    const auto result = circuitmind::simulateTransientRc(circuit, {0.1, 0.5, 10});
    assert(result.step_count == 5);
    // Series RL, with backward Euler: i[n] = (i[n-1] + dt*V/L) / (1 + dt*R/L).
    double current = 0.0;
    for (std::size_t step = 1; step <= 5; ++step) {
        current = (current + 0.1) / 1.1;
        const auto source = result.node_voltages_v[step * 3 + 1];
        const auto inductor_top = result.node_voltages_v[step * 3 + 2];
        assert(std::abs((source - inductor_top) - current) < 1e-11);
    }
    assert(result.maximum_equation_residual < 1e-10);
}

void testDigitalTruthTableAndCAbi() {
    using circuitmind::DigitalCircuit;
    using circuitmind::DigitalGate;
    using circuitmind::DigitalGateKind;
    DigitalCircuit circuit{5, {0, 1}, {2, 4}, {
        {DigitalGateKind::And, 3, 0, 4}, // Deliberately listed before its NAND dependency.
        {DigitalGateKind::Nand, 0, 1, 3},
        {DigitalGateKind::Xor, 0, 1, 2},
    }};
    const auto solved = circuitmind::simulateDigitalTruthTable(circuit);
    const std::uint8_t expected[] = {0, 0, 1, 0, 1, 1, 0, 0};
    assert(solved.row_count == 4 && solved.output_count == 2);
    for (std::size_t index = 0; index < solved.output_values.size(); ++index) {
        assert(solved.output_values[index] == expected[index]);
    }

    const cm_digital_gate_v1 gates[] = {
        {2, 3, 0, 4}, {5, 0, 1, 3}, {4, 0, 1, 2},
    };
    const std::uint32_t inputs[] = {0, 1};
    const std::uint32_t outputs[] = {2, 4};
    const cm_digital_truth_table_request_v1 request{1, 5, 3, gates, 2, inputs, 2, outputs};
    std::uint8_t output_values[8]{};
    cm_digital_truth_table_result_v1 output{output_values, 8, 0, 0};
    char error[256]{};
    const int status = cm_engine_simulate_digital_truth_table_v1(&request, &output, error, sizeof(error));
    assert(status == 0);
    assert(output.row_count == 4 && output.output_count == 2);
    for (std::size_t index = 0; index < 8; ++index) assert(output_values[index] == expected[index]);
    output.output_capacity = 1;
    const int short_buffer_status = cm_engine_simulate_digital_truth_table_v1(&request, &output, error, sizeof(error));
    assert(short_buffer_status != 0);
    assert(std::string_view(error).find("too small") != std::string_view::npos);

    bool cycle_rejected = false;
    try {
        DigitalCircuit cyclic{3, {0}, {1}, {
            {DigitalGateKind::Buffer, 2, 0, 1},
            {DigitalGateKind::Not, 1, 0, 2},
        }};
        (void)circuitmind::simulateDigitalTruthTable(cyclic);
    } catch (const circuitmind::SolverError&) { cycle_rejected = true; }
    assert(cycle_rejected);
}

void testDigitalSampledWaveformAndCAbi() {
    using circuitmind::DigitalCircuit;
    using circuitmind::DigitalGate;
    using circuitmind::DigitalGateKind;
    // The DFF breaks the combinational dependency graph; Q-bar is recomputed after each edge.
    DigitalCircuit circuit{4, {0, 1}, {2, 3}, {
        {DigitalGateKind::DFlipFlop, 0, 1, 2, 0},
        {DigitalGateKind::Not, 2, 0, 3, 0},
    }};
    const std::vector<std::uint8_t> samples{
        0, 0, // initial Q=0
        1, 0, // D changes, no edge
        1, 1, // rising edge captures 1
        0, 1, // no new edge, Q holds
        0, 0, // falling edge
        0, 1, // rising edge captures 0
    };
    const auto solved = circuitmind::simulateDigitalWaveform(circuit, samples, 6, 0.25);
    const std::uint8_t expected[] = {0, 1, 0, 1, 1, 0, 1, 0, 1, 0, 0, 1};
    assert(solved.sample_times_s.size() == 6 && solved.output_count == 2);
    for (std::size_t index = 0; index < 12; ++index) assert(solved.output_values[index] == expected[index]);
    for (std::size_t sample = 0; sample < 6; ++sample) {
        assert(std::abs(solved.sample_times_s[sample] - sample * 0.25) < 1e-15);
    }

    const cm_digital_waveform_gate_v1 gates[] = {
        {8, 0, 1, 2, 1}, {0, 2, 0, 3, 0},
    };
    const std::uint32_t inputs[] = {0, 1};
    const std::uint32_t outputs[] = {2, 3};
    const std::uint8_t native_samples[] = {0, 0, 0, 1, 1, 1};
    const cm_digital_waveform_request_v1 request{1, 4, 2, gates, 2, inputs, 2, outputs,
                                                  3, native_samples, 1.0};
    double times[3]{};
    std::uint8_t values[6]{};
    cm_digital_waveform_result_v1 output{times, 3, values, 6, 0, 0};
    char error[256]{};
    const int status = cm_engine_simulate_digital_waveform_v1(&request, &output, error, sizeof(error));
    assert(status == 0 && output.sample_count == 3 && output.output_count == 2);
    const std::uint8_t expected_native[] = {1, 0, 0, 1, 0, 1};
    for (std::size_t index = 0; index < 6; ++index) assert(values[index] == expected_native[index]);
    assert(times[0] == 0.0 && times[1] == 1.0 && times[2] == 2.0);

    const cm_digital_waveform_gate_v1 falling_gate[] = {{8, 0, 1, 2, 0, 1}};
    const std::uint8_t falling_samples[] = {0, 1, 1, 0, 0, 0, 0, 1, 0, 0};
    const cm_digital_waveform_request_v1 falling_request{1, 3, 1, falling_gate, 2, inputs, 1, outputs,
                                                          5, falling_samples, 1.0};
    double falling_times[5]{};
    std::uint8_t falling_values[5]{};
    cm_digital_waveform_result_v1 falling_output{falling_times, 5, falling_values, 5, 0, 0};
    assert(cm_engine_simulate_digital_waveform_v1(&falling_request, &falling_output, error, sizeof(error)) == 0);
    const std::uint8_t expected_falling[] = {0, 1, 1, 1, 0};
    for (std::size_t index = 0; index < 5; ++index) assert(falling_values[index] == expected_falling[index]);
    const cm_digital_waveform_gate_v1 invalid_edge_gate[] = {{8, 0, 1, 2, 0, 2}};
    const cm_digital_waveform_request_v1 invalid_edge_request{1, 3, 1, invalid_edge_gate, 2, inputs, 1, outputs,
                                                               3, native_samples, 1.0};
    assert(cm_engine_simulate_digital_waveform_v1(&invalid_edge_request, &falling_output,
                                                   error, sizeof(error)) != 0);
    assert(std::string_view(error).find("active edge") != std::string_view::npos);

    const std::uint8_t invalid_samples[] = {0, 0, 2, 1, 1, 1};
    const cm_digital_waveform_request_v1 invalid_request{1, 4, 2, gates, 2, inputs, 2, outputs,
                                                          3, invalid_samples, 1.0};
    assert(cm_engine_simulate_digital_waveform_v1(&invalid_request, &output, error, sizeof(error)) != 0);
    assert(std::string_view(error).find("binary") != std::string_view::npos);
    output.output_capacity = 1;
    assert(cm_engine_simulate_digital_waveform_v1(&request, &output, error, sizeof(error)) != 0);
    assert(std::string_view(error).find("too small") != std::string_view::npos);

    bool invalid_interval_rejected = false;
    try {
        (void)circuitmind::simulateDigitalWaveform(circuit, samples, 6, 0.0);
    } catch (const circuitmind::SolverError&) { invalid_interval_rejected = true; }
    assert(invalid_interval_rejected);
}

void testNativeDcSweepApi() {
    Circuit circuit{3, 0, {
        {ElementKind::VoltageSource, 1, 0, 1.0},
        {ElementKind::Resistor, 1, 2, 1000.0},
        {ElementKind::Resistor, 2, 0, 1000.0},
    }};
    const auto solved = circuitmind::simulateDcSweep(circuit, 0, {0.0, 5.0, 10.0});
    assert(solved.node_count == 3);
    assert(std::abs(solved.node_voltage_traces_v[2] - 0.0) < 1e-12);
    assert(std::abs(solved.node_voltage_traces_v[3 + 2] - 2.5) < 1e-12);
    assert(std::abs(solved.node_voltage_traces_v[2 * 3 + 2] - 5.0) < 1e-12);

    const cm_element_v1 elements[] = {
        {static_cast<uint32_t>(ElementKind::VoltageSource), 1, 0, 1.0, 0.0, 0.0, 0.0, 0},
        {static_cast<uint32_t>(ElementKind::Resistor), 1, 2, 1000.0, 0.0, 0.0, 0.0, 0},
        {static_cast<uint32_t>(ElementKind::Resistor), 2, 0, 1000.0, 0.0, 0.0, 0.0, 0},
    };
    const cm_dc_request_v1 dc_request{1, 3, 0, 3, elements};
    const double values[] = {0.0, 5.0, 10.0};
    const cm_dc_sweep_request_v1 request{1, dc_request, 0, 3, values};
    double node_traces[9]{}, max_residuals[3]{}, normalized_residuals[3]{};
    cm_dc_sweep_result_v1 output{node_traces, 9, max_residuals, 3, normalized_residuals, 3};
    char error[256]{};
    const int status = cm_engine_simulate_dc_sweep_v1(&request, &output, error, sizeof(error));
    assert(status == 0);
    assert(output.point_count == 3 && output.node_count == 3);
    assert(std::abs(node_traces[2] - 0.0) < 1e-12);
    assert(std::abs(node_traces[5] - 2.5) < 1e-12);
    assert(std::abs(node_traces[8] - 5.0) < 1e-12);
    assert(max_residuals[0] < 1e-10 && max_residuals[1] < 1e-10 && max_residuals[2] < 1e-10);
}

void testCAbiRoundTrip() {
    assert(cm_engine_abi_version() == 1);
    const cm_element_v1 elements[] = {
        {static_cast<uint32_t>(ElementKind::VoltageSource), 1, 0, 1.0, 0.0, 1.0, 0.0, 1},
        {static_cast<uint32_t>(ElementKind::Resistor), 1, 2, 10000.0, 0.0, 0.0, 0.0, 0},
        {static_cast<uint32_t>(ElementKind::Capacitor), 2, 0, 16e-9, 0.0, 0.0, 0.0, 0},
    };
    const cm_transient_request_v1 request{1, 3, 0, 3, elements, 8e-6, 800e-6, 2000};
    double times[101]{};
    double voltages[303]{};
    size_t samples = 0;
    double residual = 0.0;
    char error[256]{};
    const int status = cm_engine_simulate_transient_rc_v1(&request, times, 101, voltages, 303,
                                                           &samples, &residual, error, sizeof(error));
    assert(status == 0);
    assert(samples == 101);
    assert(std::abs(voltages[100 * 3 + 2] - (1.0 - std::pow(1.05, -100.0))) < 1e-11);
    assert(residual < 1e-9);
    const int short_buffer_status = cm_engine_simulate_transient_rc_v1(&request, times, 10, voltages, 303,
                                                                        &samples, &residual, error, sizeof(error));
    assert(short_buffer_status != 0);
    assert(std::string_view(error).find("too small") != std::string_view::npos);

    const cm_element_v1 dc_elements[] = {
        {static_cast<uint32_t>(ElementKind::VoltageSource), 1, 0, 1.0, 0.0, 0.0, 0.0, 0},
        {static_cast<uint32_t>(ElementKind::Resistor), 1, 0, 1000.0, 0.0, 0.0, 0.0, 0},
    };
    const cm_dc_request_v1 dc_request{1, 2, 0, 2, dc_elements};
    double dc_nodes[2]{}, dc_volts[2]{}, dc_currents[2]{}, dc_powers[2]{};
    cm_dc_result_v2 dc_result{};
    dc_result.node_voltages_v = dc_nodes; dc_result.node_capacity = 2;
    dc_result.element_voltages_v = dc_volts; dc_result.element_voltage_capacity = 2;
    dc_result.element_currents_a = dc_currents; dc_result.element_current_capacity = 2;
    dc_result.element_powers_w = dc_powers; dc_result.element_power_capacity = 2;
    const int dc_status = cm_engine_solve_dc_v2(&dc_request, &dc_result, error, sizeof(error));
    assert(dc_status == 0);
    assert(std::abs(dc_nodes[1] - 1.0) < 1e-12);
    assert(std::abs(dc_currents[0] + 1e-3) < 1e-12);
    assert(std::abs(dc_powers[0] + dc_powers[1]) < 1e-12);
    assert(dc_result.unknown_count == 2);
}

int main() {
    testVoltageDividerDc();
    testShockleyDiodeNewtonOperatingPoint();
    testPiecewiseLinearDiodeOperatingPoints();
    testIndependentCurrentSourceDirection();
    testBackwardEulerRcStep();
    testRejectedSingularAndUnboundedRequests();
    testAcSweepAndCAbi();
    testDigitalTruthTableAndCAbi();
    testDigitalSampledWaveformAndCAbi();
    testNativeDcSweepApi();
    testCAbiRoundTrip();
    std::cout << "CircuitMind C++ core tests passed\n";
}

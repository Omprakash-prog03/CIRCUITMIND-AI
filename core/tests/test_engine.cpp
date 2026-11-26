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
}

int main() {
    testVoltageDividerDc();
    testIndependentCurrentSourceDirection();
    testBackwardEulerRcStep();
    testRejectedSingularAndUnboundedRequests();
    testCAbiRoundTrip();
    std::cout << "CircuitMind C++ core tests passed\n";
}

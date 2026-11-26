#include "circuitmind/engine.hpp"

#include <cmath>
#include <iomanip>
#include <iostream>
#include <string_view>

int main(int argc, char** argv) {
    if (argc == 2 && std::string_view(argv[1]) == "--version") {
        std::cout << "CircuitMind C++ core " << circuitmind::engineVersion() << "\n";
        return 0;
    }
    if (argc != 2 || std::string_view(argv[1]) != "--self-test") {
        std::cerr << "Usage: circuitmind_engine_cli --version|--self-test\n";
        return 2;
    }
    try {
        circuitmind::Circuit circuit{3, 0, {
            {circuitmind::ElementKind::VoltageSource, 1, 0, 1.0, 0.0, 1.0, 0.0, true},
            {circuitmind::ElementKind::Resistor, 1, 2, 10000.0},
            {circuitmind::ElementKind::Capacitor, 2, 0, 16e-9, 0.0},
        }};
        const double tau = 10000.0 * 16e-9;
        const auto result = circuitmind::simulateTransientRc(circuit, {tau / 20.0, 5.0 * tau, 2000});
        const double simulated = result.node_voltages_v.back();
        const double reference = 1.0 - std::pow(1.0 + 0.05, -100.0);
        if (std::abs(simulated - reference) > 1e-12) {
            std::cerr << "RC reference check failed\n";
            return 1;
        }
        std::cout << std::setprecision(12)
                  << "C++ core " << circuitmind::engineVersion() << " self-test passed\n"
                  << "RC backward-Euler samples: " << result.time_points_s.size() << "\n"
                  << "Vout(5 tau): " << simulated << " V\n"
                  << "maximum equation residual: " << result.maximum_equation_residual << "\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Engine self-test failed: " << error.what() << "\n";
        return 1;
    }
}

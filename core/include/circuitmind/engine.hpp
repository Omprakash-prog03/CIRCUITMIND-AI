#pragma once

#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

namespace circuitmind {

enum class ElementKind : std::uint32_t {
    Resistor = 0,
    Capacitor = 1,
    VoltageSource = 2,
    CurrentSource = 3,
};

struct Element {
    ElementKind kind{};
    std::size_t node_a{};
    std::size_t node_b{};
    // R (ohm), C (farad), or constant source value (V/A), according to kind.
    double value{};
    // Capacitor initial voltage; source step low/high values when has_step is true.
    double initial_value{};
    double final_value{};
    double step_time_s{};
    bool has_step{};
};

struct Circuit {
    std::size_t node_count{}; // Includes the ground node.
    std::size_t ground_index{};
    std::vector<Element> elements;
};

struct DcResult {
    std::vector<double> node_voltages_v; // Includes ground at ground_index.
    std::vector<double> element_voltages_v; // Circuit element order.
    std::vector<double> element_currents_a; // Positive node_a -> node_b.
    std::vector<double> element_powers_w; // Positive absorbed, circuit element order.
    double maximum_equation_residual{};
    double maximum_kcl_residual_a{};
    double maximum_voltage_source_residual_v{};
    double normalized_equation_residual{};
    double total_power_balance_w{};
    double minimum_scaled_pivot{};
    double maximum_scaled_pivot{};
    std::size_t unknown_count{};
};

struct TransientOptions {
    double time_step_s{};
    double stop_time_s{};
    std::size_t maximum_steps{2000};
};

struct TransientResult {
    std::vector<double> time_points_s;
    // Row-major: sample index * node_count + node index.
    std::vector<double> node_voltages_v;
    std::size_t node_count{};
    std::size_t step_count{};
    std::size_t unknown_count{};
    double maximum_equation_residual{};
};

class SolverError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

[[nodiscard]] const char* engineVersion() noexcept;
[[nodiscard]] DcResult solveDc(const Circuit& circuit);
[[nodiscard]] TransientResult simulateTransientRc(const Circuit& circuit, const TransientOptions& options);

} // namespace circuitmind

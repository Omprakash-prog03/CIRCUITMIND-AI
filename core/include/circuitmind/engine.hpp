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
    Inductor = 4, // Accepted by AC and backward-Euler transient analysis; not DC operating point.
    Diode = 5, // DC piecewise-linear model with explicit forward drop and off resistance.
    ShockleyDiode = 6, // DC exponential junction model with explicit Is, n, and temperature.
};

struct Element {
    ElementKind kind{};
    std::size_t node_a{};
    std::size_t node_b{};
    // R (ohm), C (farad), L (henry), or constant source value (V/A), according to kind.
    double value{};
    // Capacitor initial voltage; diode off resistance; Shockley ideality factor; source step low value when has_step is true.
    double initial_value{};
    double final_value{}; // source step high value or Shockley diode temperature in kelvin.
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

struct DcSweepResult {
    // Row-major: sweep sample * node_count + node index.
    std::vector<double> node_voltage_traces_v;
    std::vector<double> maximum_equation_residuals;
    std::vector<double> normalized_equation_residuals;
    std::size_t node_count{};
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

struct AcElement {
    ElementKind kind{};
    std::size_t node_a{};
    std::size_t node_b{};
    // R (ohm), C (farad), L (henry), or AC source magnitude in RMS units (V/A).
    double value{};
    // AC source phase in radians; ignored for passive elements and required to be zero there.
    double phase_rad{};
};

struct AcCircuit {
    std::size_t node_count{};
    std::size_t ground_index{};
    std::vector<AcElement> elements;
};

struct AcSweepResult {
    std::vector<double> frequency_points_hz;
    // Row-major: sample index * node_count + node index. Complex voltages split into real/imaginary arrays.
    std::vector<double> node_voltage_real_v;
    std::vector<double> node_voltage_imag_v;
    std::vector<double> normalized_equation_residuals;
    std::size_t node_count{};
    std::size_t unknown_count{};
};

class SolverError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

[[nodiscard]] const char* engineVersion() noexcept;
[[nodiscard]] DcResult solveDc(const Circuit& circuit);
[[nodiscard]] DcSweepResult simulateDcSweep(const Circuit& circuit, std::size_t element_index,
                                            const std::vector<double>& source_values);
[[nodiscard]] TransientResult simulateTransientRc(const Circuit& circuit, const TransientOptions& options);
[[nodiscard]] AcSweepResult simulateAcSweep(const AcCircuit& circuit, const std::vector<double>& frequencies_hz);

} // namespace circuitmind

#include "circuitmind/engine.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <numeric>
#include <sstream>
#include <utility>

namespace circuitmind {
namespace {
constexpr std::size_t kMaximumElements = 100;
constexpr std::size_t kMaximumNodesIncludingGround = 25;
constexpr std::size_t kMaximumUnknowns = 32;
constexpr std::size_t kMaximumSteps = 2000;
constexpr double kPivotFloor = 1e-12;

struct LinearElement {
    ElementKind kind;
    std::size_t a;
    std::size_t b;
    double value;
};

void requireFinite(double value, const std::string& label) {
    if (!std::isfinite(value)) {
        throw SolverError(label + " is outside the finite numeric range");
    }
}

bool nearTime(double lhs, double rhs) {
    const double scale = std::max({std::abs(lhs), std::abs(rhs), 1e-30});
    return std::abs(lhs - rhs) <= 1e-12 * scale;
}

double sourceValue(const Element& element, double time_s, bool pre_step) {
    if (!element.has_step) {
        return element.value;
    }
    if (pre_step) {
        return element.initial_value;
    }
    return (time_s >= element.step_time_s || nearTime(time_s, element.step_time_s))
        ? element.final_value : element.initial_value;
}

void validateCircuitShape(const Circuit& circuit) {
    if (circuit.node_count < 2 || circuit.node_count > kMaximumNodesIncludingGround) {
        throw SolverError("circuit node_count must include ground and be between 2 and 25");
    }
    if (circuit.ground_index >= circuit.node_count) {
        throw SolverError("ground_index is outside the node range");
    }
    if (circuit.elements.empty() || circuit.elements.size() > kMaximumElements) {
        throw SolverError("circuit must contain between 1 and 100 elements");
    }
    bool ground_seen = false;
    for (std::size_t index = 0; index < circuit.elements.size(); ++index) {
        const auto& element = circuit.elements[index];
        const auto label = "element[" + std::to_string(index) + "]";
        if (element.node_a >= circuit.node_count || element.node_b >= circuit.node_count || element.node_a == element.node_b) {
            throw SolverError(label + " must connect two distinct in-range nodes");
        }
        ground_seen = ground_seen || element.node_a == circuit.ground_index || element.node_b == circuit.ground_index;
        switch (element.kind) {
        case ElementKind::Resistor:
            if (element.has_step) throw SolverError(label + " resistor cannot have waveform fields");
            requireFinite(element.value, label + " resistance");
            if (element.value <= 0.0) throw SolverError(label + " resistance must be positive");
            break;
        case ElementKind::Capacitor:
            if (element.has_step) throw SolverError(label + " capacitor cannot have waveform fields");
            requireFinite(element.value, label + " capacitance");
            requireFinite(element.initial_value, label + " initial voltage");
            if (element.value <= 0.0) throw SolverError(label + " capacitance must be positive");
            break;
        case ElementKind::VoltageSource:
        case ElementKind::CurrentSource:
            requireFinite(element.value, label + " source value");
            if (element.has_step) {
                requireFinite(element.initial_value, label + " source initial value");
                requireFinite(element.final_value, label + " source final value");
                requireFinite(element.step_time_s, label + " source step time");
                if (element.step_time_s < 0.0) throw SolverError(label + " source step time must be nonnegative");
            }
            break;
        default:
            throw SolverError(label + " has an unsupported element kind");
        }
    }
    if (!ground_seen) {
        throw SolverError("declared ground node must appear in at least one element");
    }
}

DcResult solveLinear(const Circuit& circuit, const std::vector<LinearElement>& elements) {
    const std::size_t node_unknowns = circuit.node_count - 1;
    std::vector<std::size_t> node_slot(circuit.node_count, std::numeric_limits<std::size_t>::max());
    for (std::size_t node = 0, slot = 0; node < circuit.node_count; ++node) {
        if (node != circuit.ground_index) node_slot[node] = slot++;
    }
    std::vector<std::size_t> voltage_branch(elements.size(), std::numeric_limits<std::size_t>::max());
    std::size_t voltage_count = 0;
    for (std::size_t index = 0; index < elements.size(); ++index) {
        if (elements[index].kind == ElementKind::VoltageSource) voltage_branch[index] = voltage_count++;
    }
    const std::size_t dimension = node_unknowns + voltage_count;
    if (dimension == 0 || dimension > kMaximumUnknowns) {
        throw SolverError("MNA system exceeds the 32-unknown limit");
    }
    std::vector<std::vector<double>> matrix(dimension, std::vector<double>(dimension, 0.0));
    std::vector<double> rhs(dimension, 0.0);

    for (std::size_t index = 0; index < elements.size(); ++index) {
        const auto& element = elements[index];
        const auto a = element.a;
        const auto b = element.b;
        if (element.kind == ElementKind::Resistor) {
            const double conductance = 1.0 / element.value;
            requireFinite(conductance, "resistor conductance");
            if (a != circuit.ground_index) matrix[node_slot[a]][node_slot[a]] += conductance;
            if (b != circuit.ground_index) matrix[node_slot[b]][node_slot[b]] += conductance;
            if (a != circuit.ground_index && b != circuit.ground_index) {
                matrix[node_slot[a]][node_slot[b]] -= conductance;
                matrix[node_slot[b]][node_slot[a]] -= conductance;
            }
        } else if (element.kind == ElementKind::CurrentSource) {
            // Positive current flows a -> b; RHS is current injected into each node.
            if (a != circuit.ground_index) rhs[node_slot[a]] -= element.value;
            if (b != circuit.ground_index) rhs[node_slot[b]] += element.value;
        } else if (element.kind == ElementKind::VoltageSource) {
            const std::size_t branch = node_unknowns + voltage_branch[index];
            if (a != circuit.ground_index) {
                matrix[node_slot[a]][branch] += 1.0;
                matrix[branch][node_slot[a]] += 1.0;
            }
            if (b != circuit.ground_index) {
                matrix[node_slot[b]][branch] -= 1.0;
                matrix[branch][node_slot[b]] -= 1.0;
            }
            rhs[branch] = element.value;
        }
        // Capacitors are never passed to the linear DC stamper; they are represented
        // as explicit voltage constraints at t=0 or BE companions at later samples.
        requireFinite(rhs.empty() ? 0.0 : rhs[0], "MNA right-hand side");
    }

    const auto original_matrix = matrix;
    const auto original_rhs = rhs;
    for (std::size_t row = 0; row < dimension; ++row) {
        double scale = 0.0;
        for (double coefficient : matrix[row]) scale = std::max(scale, std::abs(coefficient));
        if (scale == 0.0 || !std::isfinite(scale)) {
            throw SolverError("MNA matrix is singular: an equation has no effective coefficients");
        }
        for (double& coefficient : matrix[row]) coefficient /= scale;
        rhs[row] /= scale;
        requireFinite(rhs[row], "scaled MNA right-hand side");
    }
    for (std::size_t column = 0; column < dimension; ++column) {
        std::size_t pivot_row = column;
        for (std::size_t row = column + 1; row < dimension; ++row) {
            if (std::abs(matrix[row][column]) > std::abs(matrix[pivot_row][column])) pivot_row = row;
        }
        const double pivot = std::abs(matrix[pivot_row][column]);
        if (!std::isfinite(pivot) || pivot < kPivotFloor) {
            throw SolverError("MNA matrix is singular or numerically ill-conditioned; no result reported");
        }
        if (pivot_row != column) {
            std::swap(matrix[pivot_row], matrix[column]);
            std::swap(rhs[pivot_row], rhs[column]);
        }
        const double divisor = matrix[column][column];
        for (std::size_t row = column + 1; row < dimension; ++row) {
            const double factor = matrix[row][column] / divisor;
            matrix[row][column] = 0.0;
            for (std::size_t entry = column + 1; entry < dimension; ++entry) {
                matrix[row][entry] -= factor * matrix[column][entry];
                requireFinite(matrix[row][entry], "Gaussian elimination intermediate");
            }
            rhs[row] -= factor * rhs[column];
            requireFinite(rhs[row], "Gaussian elimination right-hand side");
        }
    }
    std::vector<double> solution(dimension, 0.0);
    for (std::size_t reverse = dimension; reverse > 0; --reverse) {
        const std::size_t row = reverse - 1;
        double remainder = rhs[row];
        for (std::size_t column = row + 1; column < dimension; ++column) remainder -= matrix[row][column] * solution[column];
        solution[row] = remainder / matrix[row][row];
        requireFinite(solution[row], "MNA solution");
    }
    double max_residual = 0.0;
    for (std::size_t row = 0; row < dimension; ++row) {
        double actual = 0.0;
        for (std::size_t column = 0; column < dimension; ++column) actual += original_matrix[row][column] * solution[column];
        requireFinite(actual, "MNA residual calculation");
        max_residual = std::max(max_residual, std::abs(actual - original_rhs[row]));
    }
    std::vector<double> node_voltages(circuit.node_count, 0.0);
    for (std::size_t node = 0; node < circuit.node_count; ++node) {
        if (node != circuit.ground_index) node_voltages[node] = solution[node_slot[node]];
    }
    return {std::move(node_voltages), max_residual, dimension};
}

} // namespace

const char* engineVersion() noexcept {
    return "0.1.0";
}

DcResult solveDc(const Circuit& circuit) {
    validateCircuitShape(circuit);
    std::vector<LinearElement> linear;
    linear.reserve(circuit.elements.size());
    for (const auto& element : circuit.elements) {
        if (element.kind == ElementKind::Capacitor) {
            throw SolverError("DC operating point does not support capacitors; request an RC transient solve with explicit initial conditions");
        }
        if ((element.kind == ElementKind::VoltageSource || element.kind == ElementKind::CurrentSource) && element.has_step) {
            throw SolverError("DC operating point accepts constant independent sources only; use transient analysis for source waveforms");
        }
        linear.push_back({element.kind, element.node_a, element.node_b, element.value});
    }
    return solveLinear(circuit, linear);
}

TransientResult simulateTransientRc(const Circuit& circuit, const TransientOptions& options) {
    validateCircuitShape(circuit);
    requireFinite(options.time_step_s, "time step");
    requireFinite(options.stop_time_s, "stop time");
    if (options.time_step_s <= 0.0 || options.stop_time_s <= 0.0) {
        throw SolverError("time step and stop time must be positive");
    }
    if (options.maximum_steps == 0 || options.maximum_steps > kMaximumSteps) {
        throw SolverError("maximum_steps must be between 1 and 2000");
    }
    const double ratio = options.stop_time_s / options.time_step_s;
    requireFinite(ratio, "stop_time / time_step");
    const double rounded = std::round(ratio);
    if (rounded < 1.0 || rounded > static_cast<double>(options.maximum_steps) ||
        std::abs(ratio - rounded) > 1e-10 * std::max(1.0, std::abs(ratio))) {
        throw SolverError("stop time must be an integer multiple of time step within the configured step limit");
    }
    const auto steps = static_cast<std::size_t>(rounded);
    for (const auto& element : circuit.elements) {
        if ((element.kind == ElementKind::VoltageSource || element.kind == ElementKind::CurrentSource) &&
            element.has_step && element.step_time_s > options.stop_time_s &&
            !nearTime(element.step_time_s, options.stop_time_s)) {
            throw SolverError("source step_time must not exceed stop_time");
        }
    }
    std::size_t capacitor_count = 0;
    std::size_t independent_voltage_count = 0;
    for (const auto& element : circuit.elements) {
        capacitor_count += element.kind == ElementKind::Capacitor ? 1U : 0U;
        independent_voltage_count += element.kind == ElementKind::VoltageSource ? 1U : 0U;
        if (element.kind == ElementKind::Capacitor && element.has_step) {
            throw SolverError("capacitors cannot have source waveform fields");
        }
    }
    if (capacitor_count == 0) throw SolverError("linear RC transient requires at least one capacitor");
    const std::size_t unknowns = circuit.node_count - 1 + independent_voltage_count + capacitor_count;
    if (unknowns > kMaximumUnknowns) throw SolverError("transient network exceeds the 32-unknown solve limit");

    // Establish t=0- from explicit capacitor voltages and pre-step source values.
    std::vector<LinearElement> initial;
    initial.reserve(circuit.elements.size());
    for (const auto& element : circuit.elements) {
        if (element.kind == ElementKind::Capacitor) {
            initial.push_back({ElementKind::VoltageSource, element.node_a, element.node_b, element.initial_value});
        } else {
            initial.push_back({element.kind, element.node_a, element.node_b, sourceValue(element, 0.0, true)});
        }
    }
    const auto initial_state = solveLinear(circuit, initial);
    std::vector<double> previous_capacitor_voltages;
    previous_capacitor_voltages.reserve(capacitor_count);
    for (const auto& element : circuit.elements) {
        if (element.kind != ElementKind::Capacitor) continue;
        const double voltage = initial_state.node_voltages_v[element.node_a] - initial_state.node_voltages_v[element.node_b];
        if (std::abs(voltage - element.initial_value) > 1e-8 * std::max(1.0, std::abs(element.initial_value))) {
            throw SolverError("capacitor initial voltage conflicts with the t=0- ideal-source constraints");
        }
        previous_capacitor_voltages.push_back(voltage);
    }

    TransientResult result;
    result.node_count = circuit.node_count;
    result.step_count = steps;
    result.unknown_count = unknowns;
    result.time_points_s.reserve(steps + 1);
    result.node_voltages_v.reserve((steps + 1) * circuit.node_count);
    result.time_points_s.push_back(0.0);
    result.node_voltages_v.insert(result.node_voltages_v.end(), initial_state.node_voltages_v.begin(), initial_state.node_voltages_v.end());
    result.maximum_equation_residual = initial_state.maximum_equation_residual;

    for (std::size_t sample = 1; sample <= steps; ++sample) {
        const double time_s = sample == steps ? options.stop_time_s : static_cast<double>(sample) * options.time_step_s;
        std::vector<LinearElement> companion;
        companion.reserve(circuit.elements.size() + capacitor_count);
        std::size_t capacitor_index = 0;
        for (const auto& element : circuit.elements) {
            if (element.kind == ElementKind::Resistor) {
                companion.push_back({ElementKind::Resistor, element.node_a, element.node_b, element.value});
            } else if (element.kind == ElementKind::Capacitor) {
                const double conductance = element.value / options.time_step_s;
                const double equivalent_resistance = options.time_step_s / element.value;
                const double equivalent_current = -conductance * previous_capacitor_voltages[capacitor_index];
                requireFinite(conductance, "capacitor companion conductance");
                requireFinite(equivalent_resistance, "capacitor companion resistance");
                requireFinite(equivalent_current, "capacitor companion current");
                if (equivalent_resistance <= 0.0) throw SolverError("capacitor companion resistance underflowed");
                companion.push_back({ElementKind::Resistor, element.node_a, element.node_b, equivalent_resistance});
                companion.push_back({ElementKind::CurrentSource, element.node_a, element.node_b, equivalent_current});
                ++capacitor_index;
            } else {
                companion.push_back({element.kind, element.node_a, element.node_b, sourceValue(element, time_s, false)});
            }
        }
        const auto state = solveLinear(circuit, companion);
        capacitor_index = 0;
        for (const auto& element : circuit.elements) {
            if (element.kind == ElementKind::Capacitor) {
                previous_capacitor_voltages[capacitor_index++] = state.node_voltages_v[element.node_a] - state.node_voltages_v[element.node_b];
            }
        }
        result.time_points_s.push_back(time_s);
        result.node_voltages_v.insert(result.node_voltages_v.end(), state.node_voltages_v.begin(), state.node_voltages_v.end());
        result.maximum_equation_residual = std::max(result.maximum_equation_residual, state.maximum_equation_residual);
    }
    return result;
}

} // namespace circuitmind

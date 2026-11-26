#include "circuitmind/engine.hpp"

#include <algorithm>
#include <cmath>
#include <complex>
#include <limits>
#include <numeric>
#include <set>
#include <sstream>
#include <utility>

namespace circuitmind {
namespace {
constexpr std::size_t kMaximumTransientElements = 100;
constexpr std::size_t kMaximumTransientNodesIncludingGround = 25;
constexpr std::size_t kMaximumTransientUnknowns = 32;
constexpr std::size_t kMaximumDcElements = 500;
constexpr std::size_t kMaximumDcNodesIncludingGround = 151;
constexpr std::size_t kMaximumDcUnknowns = 180;
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

void validateCircuitShape(const Circuit& circuit, std::size_t maximum_nodes, std::size_t maximum_elements) {
    if (circuit.node_count < 2 || circuit.node_count > maximum_nodes) {
        throw SolverError("circuit node_count is outside the configured bounded range (ground included)");
    }
    if (circuit.ground_index >= circuit.node_count) {
        throw SolverError("ground_index is outside the node range");
    }
    if (circuit.elements.empty() || circuit.elements.size() > maximum_elements) {
        throw SolverError("circuit element count is outside the configured bounded range");
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
        case ElementKind::Inductor:
            if (element.has_step) throw SolverError(label + " inductor cannot have waveform fields");
            requireFinite(element.value, label + " inductance");
            requireFinite(element.initial_value, label + " initial current");
            if (element.value <= 0.0) throw SolverError(label + " inductance must be positive");
            break;
        case ElementKind::Diode:
            if (element.has_step) throw SolverError(label + " diode cannot have waveform fields");
            requireFinite(element.value, label + " forward voltage");
            requireFinite(element.initial_value, label + " off resistance");
            if (element.value <= 0.0 || element.initial_value <= 0.0) {
                throw SolverError(label + " diode forward voltage and off resistance must be positive");
            }
            break;
        case ElementKind::ShockleyDiode:
            if (element.has_step) throw SolverError(label + " Shockley diode cannot have waveform fields");
            requireFinite(element.value, label + " saturation current");
            requireFinite(element.initial_value, label + " ideality factor");
            requireFinite(element.final_value, label + " temperature");
            if (element.value < 1e-30 || element.value > 1e-3 ||
                element.initial_value < 0.5 || element.initial_value > 2.0 ||
                element.final_value < 200.0 || element.final_value > 500.0) {
                throw SolverError(label + " Shockley parameters must satisfy Is in [1e-30,1e-3] A, n in [0.5,2], and T in [200,500] K");
            }
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

DcResult solveLinear(const Circuit& circuit, const std::vector<LinearElement>& elements, std::size_t maximum_unknowns) {
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
    if (dimension == 0 || dimension > maximum_unknowns) {
        throw SolverError("MNA system exceeds the configured unknown-count limit");
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
    double minimum_scaled_pivot = std::numeric_limits<double>::infinity();
    double maximum_scaled_pivot = 0.0;
    for (std::size_t column = 0; column < dimension; ++column) {
        std::size_t pivot_row = column;
        for (std::size_t row = column + 1; row < dimension; ++row) {
            if (std::abs(matrix[row][column]) > std::abs(matrix[pivot_row][column])) pivot_row = row;
        }
        const double pivot = std::abs(matrix[pivot_row][column]);
        if (!std::isfinite(pivot) || pivot < kPivotFloor) {
            throw SolverError("MNA matrix is singular or numerically ill-conditioned; no result reported");
        }
        minimum_scaled_pivot = std::min(minimum_scaled_pivot, pivot);
        maximum_scaled_pivot = std::max(maximum_scaled_pivot, pivot);
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
    double max_kcl_residual = 0.0;
    double max_voltage_residual = 0.0;
    double normalized_residual = 0.0;
    for (std::size_t row = 0; row < dimension; ++row) {
        double actual = 0.0;
        double scale = std::max(1e-30, std::abs(original_rhs[row]));
        for (std::size_t column = 0; column < dimension; ++column) {
            const double term = original_matrix[row][column] * solution[column];
            actual += term;
            scale += std::abs(term);
        }
        requireFinite(actual, "MNA residual calculation");
        const double residual = std::abs(actual - original_rhs[row]);
        max_residual = std::max(max_residual, residual);
        normalized_residual = std::max(normalized_residual, residual / scale);
        if (row < node_unknowns) max_kcl_residual = std::max(max_kcl_residual, residual);
        else max_voltage_residual = std::max(max_voltage_residual, residual);
    }
    std::vector<double> node_voltages(circuit.node_count, 0.0);
    for (std::size_t node = 0; node < circuit.node_count; ++node) {
        if (node != circuit.ground_index) node_voltages[node] = solution[node_slot[node]];
    }
    DcResult result;
    result.node_voltages_v = std::move(node_voltages);
    result.maximum_equation_residual = max_residual;
    result.maximum_kcl_residual_a = max_kcl_residual;
    result.maximum_voltage_source_residual_v = max_voltage_residual;
    result.normalized_equation_residual = normalized_residual;
    result.minimum_scaled_pivot = minimum_scaled_pivot;
    result.maximum_scaled_pivot = maximum_scaled_pivot;
    result.unknown_count = dimension;
    result.element_voltages_v.reserve(elements.size());
    result.element_currents_a.reserve(elements.size());
    result.element_powers_w.reserve(elements.size());
    for (std::size_t index = 0; index < elements.size(); ++index) {
        const auto& element = elements[index];
        const double voltage = result.node_voltages_v[element.a] - result.node_voltages_v[element.b];
        double current = 0.0;
        if (element.kind == ElementKind::Resistor) current = voltage / element.value;
        else if (element.kind == ElementKind::CurrentSource) current = element.value;
        else if (element.kind == ElementKind::VoltageSource) current = solution[node_unknowns + voltage_branch[index]];
        const double power = voltage * current;
        requireFinite(voltage, "element voltage");
        requireFinite(current, "element current");
        requireFinite(power, "element power");
        result.element_voltages_v.push_back(voltage);
        result.element_currents_a.push_back(current);
        result.element_powers_w.push_back(power);
        result.total_power_balance_w += power;
        requireFinite(result.total_power_balance_w, "total power balance");
    }
    return result;
}

DcResult solveShockleyDiodeCircuit(const Circuit& circuit) {
    constexpr std::size_t maximum_iterations = 100;
    constexpr double boltzmann = 1.380649e-23;
    constexpr double elementary_charge = 1.602176634e-19;
    constexpr double maximum_linearization_exponent = 40.0;
    const auto no_index = std::numeric_limits<std::size_t>::max();
    std::vector<double> junction_guesses(circuit.elements.size(), 0.0);
    std::vector<double> thermal_voltages(circuit.elements.size(), 0.0);
    for (std::size_t index = 0; index < circuit.elements.size(); ++index) {
        const auto& element = circuit.elements[index];
        if (element.kind == ElementKind::ShockleyDiode) {
            thermal_voltages[index] = element.initial_value * boltzmann * element.final_value / elementary_charge;
            requireFinite(thermal_voltages[index], "diode thermal voltage");
            if (thermal_voltages[index] <= 0.0) throw SolverError("diode thermal voltage underflowed");
        }
    }

    DcResult linear_solution;
    std::vector<std::size_t> direct_index(circuit.elements.size(), no_index);
    bool converged = false;
    for (std::size_t iteration = 0; iteration < maximum_iterations; ++iteration) {
        std::vector<LinearElement> linear;
        linear.reserve(circuit.elements.size() * 2);
        for (std::size_t index = 0; index < circuit.elements.size(); ++index) {
            const auto& element = circuit.elements[index];
            if (element.kind != ElementKind::ShockleyDiode) {
                direct_index[index] = linear.size();
                linear.push_back({element.kind, element.node_a, element.node_b, element.value});
                continue;
            }
            const double vt = thermal_voltages[index];
            const double limited_exponent = std::clamp(junction_guesses[index] / vt,
                -maximum_linearization_exponent, maximum_linearization_exponent);
            const double exp_v = std::exp(limited_exponent);
            const double current = element.value * std::expm1(limited_exponent);
            const double conductance = element.value * exp_v / vt;
            requireFinite(current, "Shockley diode linearization current");
            requireFinite(conductance, "Shockley diode incremental conductance");
            if (conductance <= 0.0) throw SolverError("Shockley diode incremental conductance underflowed");
            const double equivalent_resistance = 1.0 / conductance;
            const double history_current = current - conductance * junction_guesses[index];
            requireFinite(equivalent_resistance, "Shockley diode companion resistance");
            requireFinite(history_current, "Shockley diode history current");
            if (equivalent_resistance <= 0.0) throw SolverError("Shockley diode companion resistance underflowed");
            linear.push_back({ElementKind::Resistor, element.node_a, element.node_b, equivalent_resistance});
            linear.push_back({ElementKind::CurrentSource, element.node_a, element.node_b, history_current});
        }
        linear_solution = solveLinear(circuit, linear, kMaximumDcUnknowns);
        std::vector<double> next_guesses = junction_guesses;
        converged = true;
        for (std::size_t index = 0; index < circuit.elements.size(); ++index) {
            const auto& element = circuit.elements[index];
            if (element.kind != ElementKind::ShockleyDiode) continue;
            const double raw_voltage = linear_solution.node_voltages_v[element.node_a] -
                                       linear_solution.node_voltages_v[element.node_b];
            const double vt = thermal_voltages[index];
            const double vcrit = vt * std::log(vt / (std::sqrt(2.0) * element.value));
            double limited_voltage = raw_voltage;
            if (raw_voltage > vcrit && std::abs(raw_voltage - junction_guesses[index]) > 2.0 * vt) {
                if (junction_guesses[index] > 0.0) {
                    const double argument = 1.0 + (raw_voltage - junction_guesses[index]) / vt;
                    limited_voltage = argument > 0.0
                        ? junction_guesses[index] + vt * std::log(argument) : vcrit;
                } else if (raw_voltage > 0.0) {
                    limited_voltage = vt * std::log(raw_voltage / vt);
                }
            }
            requireFinite(limited_voltage, "limited Shockley diode voltage");
            const double tolerance = 1e-9 * std::max(1.0, std::abs(raw_voltage));
            if (std::abs(raw_voltage - junction_guesses[index]) > tolerance ||
                std::abs(raw_voltage - limited_voltage) > tolerance) {
                converged = false;
            }
            next_guesses[index] = limited_voltage;
        }
        if (converged) break;
        junction_guesses = std::move(next_guesses);
    }
    if (!converged) throw SolverError("Shockley diode Newton solve did not converge within 100 iterations");

    DcResult result = linear_solution;
    result.element_voltages_v.assign(circuit.elements.size(), 0.0);
    result.element_currents_a.assign(circuit.elements.size(), 0.0);
    result.element_powers_w.assign(circuit.elements.size(), 0.0);
    result.total_power_balance_w = 0.0;
    result.maximum_equation_residual = 0.0;
    result.maximum_kcl_residual_a = 0.0;
    result.maximum_voltage_source_residual_v = 0.0;
    result.normalized_equation_residual = 0.0;
    std::vector<double> node_kcl(circuit.node_count, 0.0);
    std::vector<double> node_current_scale(circuit.node_count, 1e-30);
    for (std::size_t index = 0; index < circuit.elements.size(); ++index) {
        const auto& element = circuit.elements[index];
        const double voltage = result.node_voltages_v[element.node_a] - result.node_voltages_v[element.node_b];
        double current = 0.0;
        if (element.kind == ElementKind::Resistor) {
            current = voltage / element.value;
        } else if (element.kind == ElementKind::CurrentSource) {
            current = element.value;
        } else if (element.kind == ElementKind::VoltageSource) {
            current = linear_solution.element_currents_a[direct_index[index]];
            result.maximum_voltage_source_residual_v = std::max(result.maximum_voltage_source_residual_v,
                                                                 std::abs(voltage - element.value));
        } else if (element.kind == ElementKind::ShockleyDiode) {
            const double exponent = voltage / thermal_voltages[index];
            if (exponent > maximum_linearization_exponent) {
                throw SolverError("Shockley diode voltage exceeds the configured exponential range");
            }
            current = element.value * std::expm1(std::max(exponent, -700.0));
        }
        requireFinite(voltage, "Shockley DC element voltage");
        requireFinite(current, "Shockley DC element current");
        const double power = voltage * current;
        requireFinite(power, "Shockley DC element power");
        result.element_voltages_v[index] = voltage;
        result.element_currents_a[index] = current;
        result.element_powers_w[index] = power;
        result.total_power_balance_w += power;
        requireFinite(result.total_power_balance_w, "Shockley DC power balance");
        node_kcl[element.node_a] += current;
        node_kcl[element.node_b] -= current;
        node_current_scale[element.node_a] += std::abs(current);
        node_current_scale[element.node_b] += std::abs(current);
    }
    for (std::size_t node = 0; node < circuit.node_count; ++node) {
        if (node == circuit.ground_index) continue;
        result.maximum_kcl_residual_a = std::max(result.maximum_kcl_residual_a, std::abs(node_kcl[node]));
        result.normalized_equation_residual = std::max(result.normalized_equation_residual,
            std::abs(node_kcl[node]) / node_current_scale[node]);
    }
    result.maximum_equation_residual = std::max(result.maximum_kcl_residual_a,
                                                 result.maximum_voltage_source_residual_v);
    result.normalized_equation_residual = std::max(result.normalized_equation_residual,
        result.maximum_voltage_source_residual_v);
    return result;
}

} // namespace

const char* engineVersion() noexcept {
    return "0.2.0";
}

DcResult solveDc(const Circuit& circuit) {
    validateCircuitShape(circuit, kMaximumDcNodesIncludingGround, kMaximumDcElements);
    std::size_t diode_count = 0;
    std::size_t shockley_diode_count = 0;
    for (const auto& element : circuit.elements) {
        if (element.kind == ElementKind::Capacitor || element.kind == ElementKind::Inductor) {
            throw SolverError("DC operating point does not support capacitors or inductors; request a supported analysis with explicit conditions");
        }
        if ((element.kind == ElementKind::VoltageSource || element.kind == ElementKind::CurrentSource) && element.has_step) {
            throw SolverError("DC operating point accepts constant independent sources only; use transient analysis for source waveforms");
        }
        diode_count += element.kind == ElementKind::Diode ? 1U : 0U;
        shockley_diode_count += element.kind == ElementKind::ShockleyDiode ? 1U : 0U;
    }
    if (shockley_diode_count > 0) {
        if (diode_count > 0) throw SolverError("mixing piecewise-linear and Shockley diode models in one solve is not supported");
        return solveShockleyDiodeCircuit(circuit);
    }
    if (diode_count == 0) {
        std::vector<LinearElement> linear;
        linear.reserve(circuit.elements.size());
        for (const auto& element : circuit.elements) {
            linear.push_back({element.kind, element.node_a, element.node_b, element.value});
        }
        return solveLinear(circuit, linear, kMaximumDcUnknowns);
    }

    constexpr std::size_t maximum_active_set_iterations = 100;
    std::vector<bool> conducting(circuit.elements.size(), false);
    std::set<std::vector<bool>> visited_states;
    const auto no_index = std::numeric_limits<std::size_t>::max();
    for (std::size_t iteration = 0; iteration < maximum_active_set_iterations; ++iteration) {
        if (!visited_states.insert(conducting).second) {
            throw SolverError("piecewise-linear diode active set cycled; no unique operating point reported");
        }
        std::vector<LinearElement> linear;
        std::vector<std::size_t> direct_index(circuit.elements.size(), no_index);
        std::vector<std::size_t> diode_branch_index(circuit.elements.size(), no_index);
        std::vector<std::size_t> diode_leakage_index(circuit.elements.size(), no_index);
        linear.reserve(circuit.elements.size() + diode_count);
        for (std::size_t index = 0; index < circuit.elements.size(); ++index) {
            const auto& element = circuit.elements[index];
            if (element.kind != ElementKind::Diode) {
                direct_index[index] = linear.size();
                linear.push_back({element.kind, element.node_a, element.node_b, element.value});
                continue;
            }
            if (conducting[index]) {
                diode_branch_index[index] = linear.size();
                linear.push_back({ElementKind::VoltageSource, element.node_a, element.node_b, element.value});
            }
            diode_leakage_index[index] = linear.size();
            linear.push_back({ElementKind::Resistor, element.node_a, element.node_b, element.initial_value});
        }
        const auto linear_solution = solveLinear(circuit, linear, kMaximumDcUnknowns);
        std::vector<bool> next_state = conducting;
        bool state_changed = false;
        for (std::size_t index = 0; index < circuit.elements.size(); ++index) {
            const auto& diode = circuit.elements[index];
            if (diode.kind != ElementKind::Diode) continue;
            const double voltage = linear_solution.node_voltages_v[diode.node_a] -
                                   linear_solution.node_voltages_v[diode.node_b];
            const double leakage_current = voltage / diode.initial_value;
            const double diode_current = leakage_current +
                (conducting[index] ? linear_solution.element_currents_a[diode_branch_index[index]] : 0.0);
            const double voltage_tolerance = 1e-10 * std::max(1.0, diode.value);
            const double current_tolerance = 1e-12 * std::max(1.0, std::abs(diode_current));
            if (!conducting[index] && voltage > diode.value + voltage_tolerance) {
                next_state[index] = true;
                state_changed = true;
            } else if (conducting[index] && diode_current < -current_tolerance) {
                next_state[index] = false;
                state_changed = true;
            }
        }
        if (state_changed) {
            conducting = std::move(next_state);
            continue;
        }

        DcResult result = linear_solution;
        result.element_voltages_v.assign(circuit.elements.size(), 0.0);
        result.element_currents_a.assign(circuit.elements.size(), 0.0);
        result.element_powers_w.assign(circuit.elements.size(), 0.0);
        result.total_power_balance_w = 0.0;
        for (std::size_t index = 0; index < circuit.elements.size(); ++index) {
            const auto& element = circuit.elements[index];
            const double voltage = result.node_voltages_v[element.node_a] - result.node_voltages_v[element.node_b];
            double current = 0.0;
            if (element.kind == ElementKind::Diode) {
                current = voltage / element.initial_value;
                if (conducting[index]) current += linear_solution.element_currents_a[diode_branch_index[index]];
            } else {
                current = linear_solution.element_currents_a[direct_index[index]];
            }
            const double power = voltage * current;
            requireFinite(voltage, "DC element voltage");
            requireFinite(current, "DC element current");
            requireFinite(power, "DC element power");
            result.element_voltages_v[index] = voltage;
            result.element_currents_a[index] = current;
            result.element_powers_w[index] = power;
            result.total_power_balance_w += power;
            requireFinite(result.total_power_balance_w, "DC power balance");
        }
        return result;
    }
    throw SolverError("piecewise-linear diode operating point did not converge within 100 active-set iterations");
}

DcSweepResult simulateDcSweep(const Circuit& circuit, std::size_t element_index,
                              const std::vector<double>& source_values) {
    if (source_values.size() < 2 || source_values.size() > 200) {
        throw SolverError("DC source sweep must contain between 2 and 200 points");
    }
    if (element_index >= circuit.elements.size()) {
        throw SolverError("DC sweep element index is outside the circuit element range");
    }
    const auto kind = circuit.elements[element_index].kind;
    if (kind != ElementKind::VoltageSource && kind != ElementKind::CurrentSource) {
        throw SolverError("DC sweep target must be an independent voltage or current source");
    }
    for (std::size_t index = 0; index < source_values.size(); ++index) {
        requireFinite(source_values[index], "DC sweep source value");
        if (index > 0 && source_values[index] <= source_values[index - 1]) {
            throw SolverError("DC sweep source values must be strictly increasing");
        }
    }
    DcSweepResult result;
    result.node_count = circuit.node_count;
    result.node_voltage_traces_v.reserve(source_values.size() * circuit.node_count);
    result.maximum_equation_residuals.reserve(source_values.size());
    result.normalized_equation_residuals.reserve(source_values.size());
    for (std::size_t point = 0; point < source_values.size(); ++point) {
        Circuit point_circuit = circuit;
        point_circuit.elements[element_index].value = source_values[point];
        try {
            const auto solved = solveDc(point_circuit);
            if (point == 0) result.unknown_count = solved.unknown_count;
            result.node_voltage_traces_v.insert(result.node_voltage_traces_v.end(),
                                                solved.node_voltages_v.begin(), solved.node_voltages_v.end());
            result.maximum_equation_residuals.push_back(solved.maximum_equation_residual);
            result.normalized_equation_residuals.push_back(solved.normalized_equation_residual);
        } catch (const SolverError& error) {
            std::ostringstream message;
            message << "DC sweep failed at point " << (point + 1) << "/" << source_values.size()
                    << " (" << source_values[point] << "): " << error.what();
            throw SolverError(message.str());
        }
    }
    return result;
}

TransientResult simulateTransientRc(const Circuit& circuit, const TransientOptions& options) {
    validateCircuitShape(circuit, kMaximumTransientNodesIncludingGround, kMaximumTransientElements);
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
        if (element.kind == ElementKind::Diode || element.kind == ElementKind::ShockleyDiode) {
            throw SolverError("transient analysis supports linear R/C/L and independent-source elements only; diode models are DC-only");
        }
    }
    for (const auto& element : circuit.elements) {
        if ((element.kind == ElementKind::VoltageSource || element.kind == ElementKind::CurrentSource) &&
            element.has_step && element.step_time_s > options.stop_time_s &&
            !nearTime(element.step_time_s, options.stop_time_s)) {
            throw SolverError("source step_time must not exceed stop_time");
        }
    }
    std::size_t capacitor_count = 0;
    std::size_t inductor_count = 0;
    std::size_t independent_voltage_count = 0;
    for (const auto& element : circuit.elements) {
        capacitor_count += element.kind == ElementKind::Capacitor ? 1U : 0U;
        inductor_count += element.kind == ElementKind::Inductor ? 1U : 0U;
        independent_voltage_count += element.kind == ElementKind::VoltageSource ? 1U : 0U;
        if ((element.kind == ElementKind::Capacitor || element.kind == ElementKind::Inductor) && element.has_step) {
            throw SolverError("capacitors and inductors cannot have source waveform fields");
        }
    }
    if (capacitor_count + inductor_count == 0) {
        throw SolverError("linear RLC transient requires at least one capacitor or inductor");
    }
    const std::size_t unknowns = circuit.node_count - 1 + independent_voltage_count + capacitor_count;
    if (unknowns > kMaximumTransientUnknowns) throw SolverError("transient network exceeds the 32-unknown solve limit");

    // Establish t=0- from explicit capacitor voltages and pre-step source values.
    std::vector<LinearElement> initial;
    initial.reserve(circuit.elements.size());
    for (const auto& element : circuit.elements) {
        if (element.kind == ElementKind::Capacitor) {
            initial.push_back({ElementKind::VoltageSource, element.node_a, element.node_b, element.initial_value});
        } else if (element.kind == ElementKind::Inductor) {
            initial.push_back({ElementKind::CurrentSource, element.node_a, element.node_b, element.initial_value});
        } else {
            initial.push_back({element.kind, element.node_a, element.node_b, sourceValue(element, 0.0, true)});
        }
    }
    const auto initial_state = solveLinear(circuit, initial, kMaximumTransientUnknowns);
    std::vector<double> previous_capacitor_voltages;
    previous_capacitor_voltages.reserve(capacitor_count);
    std::vector<double> previous_inductor_currents;
    previous_inductor_currents.reserve(inductor_count);
    for (const auto& element : circuit.elements) {
        if (element.kind != ElementKind::Capacitor) continue;
        const double voltage = initial_state.node_voltages_v[element.node_a] - initial_state.node_voltages_v[element.node_b];
        if (std::abs(voltage - element.initial_value) > 1e-8 * std::max(1.0, std::abs(element.initial_value))) {
            throw SolverError("capacitor initial voltage conflicts with the t=0- ideal-source constraints");
        }
        previous_capacitor_voltages.push_back(voltage);
    }
    for (const auto& element : circuit.elements) {
        if (element.kind != ElementKind::Inductor) continue;
        previous_inductor_currents.push_back(element.initial_value);
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
        std::size_t inductor_index = 0;
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
            } else if (element.kind == ElementKind::Inductor) {
                const double conductance = options.time_step_s / element.value;
                const double equivalent_resistance = element.value / options.time_step_s;
                const double equivalent_current = previous_inductor_currents[inductor_index];
                requireFinite(conductance, "inductor companion conductance");
                requireFinite(equivalent_resistance, "inductor companion resistance");
                requireFinite(equivalent_current, "inductor companion current");
                if (conductance <= 0.0 || equivalent_resistance <= 0.0) {
                    throw SolverError("inductor companion values underflowed");
                }
                companion.push_back({ElementKind::Resistor, element.node_a, element.node_b, equivalent_resistance});
                companion.push_back({ElementKind::CurrentSource, element.node_a, element.node_b, equivalent_current});
                ++inductor_index;
            } else {
                companion.push_back({element.kind, element.node_a, element.node_b, sourceValue(element, time_s, false)});
            }
        }
        const auto state = solveLinear(circuit, companion, kMaximumTransientUnknowns);
        capacitor_index = 0;
        inductor_index = 0;
        for (const auto& element : circuit.elements) {
            if (element.kind == ElementKind::Capacitor) {
                previous_capacitor_voltages[capacitor_index++] = state.node_voltages_v[element.node_a] - state.node_voltages_v[element.node_b];
            } else if (element.kind == ElementKind::Inductor) {
                const double voltage = state.node_voltages_v[element.node_a] - state.node_voltages_v[element.node_b];
                previous_inductor_currents[inductor_index] += options.time_step_s / element.value * voltage;
                requireFinite(previous_inductor_currents[inductor_index], "inductor current state");
                ++inductor_index;
            }
        }
        result.time_points_s.push_back(time_s);
        result.node_voltages_v.insert(result.node_voltages_v.end(), state.node_voltages_v.begin(), state.node_voltages_v.end());
        result.maximum_equation_residual = std::max(result.maximum_equation_residual, state.maximum_equation_residual);
    }
    return result;
}

AcSweepResult simulateAcSweep(const AcCircuit& circuit, const std::vector<double>& frequencies_hz) {
    constexpr std::size_t maximum_elements = 200;
    constexpr std::size_t maximum_nodes_including_ground = 65;
    constexpr std::size_t maximum_unknowns = 80;
    constexpr std::size_t maximum_frequency_points = 200;
    using Complex = std::complex<double>;
    constexpr double pi = 3.141592653589793238462643383279502884;
    const auto requireComplexFinite = [](const Complex& value, const std::string& label) {
        if (!std::isfinite(value.real()) || !std::isfinite(value.imag())) {
            throw SolverError(label + " is outside the finite numeric range");
        }
    };

    if (circuit.node_count < 2 || circuit.node_count > maximum_nodes_including_ground) {
        throw SolverError("AC circuit node_count must include ground and be between 2 and 65");
    }
    if (circuit.ground_index >= circuit.node_count) throw SolverError("AC ground_index is outside the node range");
    if (circuit.elements.empty() || circuit.elements.size() > maximum_elements) {
        throw SolverError("AC circuit must contain between 1 and 200 elements");
    }
    if (frequencies_hz.empty() || frequencies_hz.size() > maximum_frequency_points) {
        throw SolverError("AC sweep must contain between 1 and 200 frequency points");
    }
    for (std::size_t index = 0; index < frequencies_hz.size(); ++index) {
        requireFinite(frequencies_hz[index], "AC frequency");
        if (frequencies_hz[index] <= 0.0) throw SolverError("AC frequencies must be positive");
        if (index > 0 && frequencies_hz[index] <= frequencies_hz[index - 1]) {
            throw SolverError("AC frequencies must be strictly increasing");
        }
    }

    bool ground_seen = false;
    std::size_t voltage_source_count = 0;
    std::size_t source_count = 0;
    std::vector<std::size_t> voltage_branch(circuit.elements.size(), std::numeric_limits<std::size_t>::max());
    for (std::size_t index = 0; index < circuit.elements.size(); ++index) {
        const auto& element = circuit.elements[index];
        const auto label = "AC element[" + std::to_string(index) + "]";
        if (element.node_a >= circuit.node_count || element.node_b >= circuit.node_count || element.node_a == element.node_b) {
            throw SolverError(label + " must connect two distinct in-range nodes");
        }
        ground_seen = ground_seen || element.node_a == circuit.ground_index || element.node_b == circuit.ground_index;
        requireFinite(element.value, label + " value");
        requireFinite(element.phase_rad, label + " phase");
        switch (element.kind) {
        case ElementKind::Resistor:
        case ElementKind::Capacitor:
        case ElementKind::Inductor:
            if (element.value <= 0.0) throw SolverError(label + " passive value must be positive");
            if (element.phase_rad != 0.0) throw SolverError(label + " passive element phase must be zero");
            break;
        case ElementKind::VoltageSource:
            if (element.value < 0.0) throw SolverError(label + " AC magnitude must not be negative");
            voltage_branch[index] = voltage_source_count++;
            ++source_count;
            break;
        case ElementKind::CurrentSource:
            if (element.value < 0.0) throw SolverError(label + " AC magnitude must not be negative");
            ++source_count;
            break;
        default:
            throw SolverError(label + " has an unsupported kind");
        }
    }
    if (!ground_seen) throw SolverError("declared AC ground must appear in at least one element");
    if (source_count == 0) throw SolverError("AC sweep requires at least one independent AC source");

    const std::size_t node_unknowns = circuit.node_count - 1;
    const std::size_t dimension = node_unknowns + voltage_source_count;
    if (dimension == 0 || dimension > maximum_unknowns) {
        throw SolverError("AC MNA system exceeds the 80-unknown limit");
    }
    std::vector<std::size_t> node_slot(circuit.node_count, std::numeric_limits<std::size_t>::max());
    for (std::size_t node = 0, slot = 0; node < circuit.node_count; ++node) {
        if (node != circuit.ground_index) node_slot[node] = slot++;
    }

    AcSweepResult result;
    result.frequency_points_hz = frequencies_hz;
    result.node_count = circuit.node_count;
    result.unknown_count = dimension;
    const auto output_count = frequencies_hz.size() * circuit.node_count;
    result.node_voltage_real_v.assign(output_count, 0.0);
    result.node_voltage_imag_v.assign(output_count, 0.0);
    result.normalized_equation_residuals.reserve(frequencies_hz.size());

    for (std::size_t sample = 0; sample < frequencies_hz.size(); ++sample) {
        const double omega = 2.0 * pi * frequencies_hz[sample];
        requireFinite(omega, "AC angular frequency");
        std::vector<std::vector<Complex>> matrix(dimension, std::vector<Complex>(dimension, Complex{}));
        std::vector<Complex> rhs(dimension, Complex{});
        for (std::size_t index = 0; index < circuit.elements.size(); ++index) {
            const auto& element = circuit.elements[index];
            const auto a = element.node_a;
            const auto b = element.node_b;
            if (element.kind == ElementKind::Resistor || element.kind == ElementKind::Capacitor ||
                element.kind == ElementKind::Inductor) {
                Complex admittance{};
                if (element.kind == ElementKind::Resistor) {
                    admittance = {1.0 / element.value, 0.0};
                } else if (element.kind == ElementKind::Capacitor) {
                    admittance = {0.0, omega * element.value};
                } else {
                    const double reactance = omega * element.value;
                    requireFinite(reactance, "inductor reactance");
                    if (reactance <= 0.0) throw SolverError("inductor reactance underflowed");
                    admittance = {0.0, -1.0 / reactance};
                }
                requireComplexFinite(admittance, "AC element admittance");
                if (a != circuit.ground_index) matrix[node_slot[a]][node_slot[a]] += admittance;
                if (b != circuit.ground_index) matrix[node_slot[b]][node_slot[b]] += admittance;
                if (a != circuit.ground_index && b != circuit.ground_index) {
                    matrix[node_slot[a]][node_slot[b]] -= admittance;
                    matrix[node_slot[b]][node_slot[a]] -= admittance;
                }
            } else {
                const Complex phasor = std::polar(element.value, element.phase_rad);
                requireComplexFinite(phasor, "AC source phasor");
                if (element.kind == ElementKind::CurrentSource) {
                    if (a != circuit.ground_index) rhs[node_slot[a]] -= phasor;
                    if (b != circuit.ground_index) rhs[node_slot[b]] += phasor;
                } else {
                    const std::size_t branch = node_unknowns + voltage_branch[index];
                    if (a != circuit.ground_index) {
                        matrix[node_slot[a]][branch] += 1.0;
                        matrix[branch][node_slot[a]] += 1.0;
                    }
                    if (b != circuit.ground_index) {
                        matrix[node_slot[b]][branch] -= 1.0;
                        matrix[branch][node_slot[b]] -= 1.0;
                    }
                    rhs[branch] = phasor;
                }
            }
        }
        const auto original_matrix = matrix;
        const auto original_rhs = rhs;
        for (std::size_t row = 0; row < dimension; ++row) {
            double scale = 0.0;
            for (const auto& coefficient : matrix[row]) {
                requireComplexFinite(coefficient, "AC MNA matrix coefficient");
                scale = std::max(scale, std::abs(coefficient));
            }
            requireComplexFinite(rhs[row], "AC MNA right-hand side");
            if (!std::isfinite(scale) || scale == 0.0) {
                throw SolverError("AC MNA matrix is singular: an equation has no effective coefficients");
            }
            for (auto& coefficient : matrix[row]) coefficient /= scale;
            rhs[row] /= scale;
            requireComplexFinite(rhs[row], "scaled AC MNA right-hand side");
        }
        for (std::size_t column = 0; column < dimension; ++column) {
            std::size_t pivot_row = column;
            for (std::size_t row = column + 1; row < dimension; ++row) {
                if (std::abs(matrix[row][column]) > std::abs(matrix[pivot_row][column])) pivot_row = row;
            }
            const double pivot = std::abs(matrix[pivot_row][column]);
            if (!std::isfinite(pivot) || pivot < kPivotFloor) {
                throw SolverError("AC MNA matrix is singular or numerically ill-conditioned; no response reported");
            }
            if (pivot_row != column) {
                std::swap(matrix[pivot_row], matrix[column]);
                std::swap(rhs[pivot_row], rhs[column]);
            }
            const Complex divisor = matrix[column][column];
            for (std::size_t row = column + 1; row < dimension; ++row) {
                const Complex factor = matrix[row][column] / divisor;
                matrix[row][column] = Complex{};
                for (std::size_t entry = column + 1; entry < dimension; ++entry) {
                    matrix[row][entry] -= factor * matrix[column][entry];
                    requireComplexFinite(matrix[row][entry], "AC Gaussian elimination intermediate");
                }
                rhs[row] -= factor * rhs[column];
                requireComplexFinite(rhs[row], "AC Gaussian elimination right-hand side");
            }
        }
        std::vector<Complex> solution(dimension, Complex{});
        for (std::size_t reverse = dimension; reverse > 0; --reverse) {
            const std::size_t row = reverse - 1;
            Complex remainder = rhs[row];
            for (std::size_t column = row + 1; column < dimension; ++column) {
                remainder -= matrix[row][column] * solution[column];
            }
            solution[row] = remainder / matrix[row][row];
            requireComplexFinite(solution[row], "AC MNA solution");
        }
        double normalized_residual = 0.0;
        for (std::size_t row = 0; row < dimension; ++row) {
            Complex actual{};
            double scale = std::max(1e-30, std::abs(original_rhs[row]));
            for (std::size_t column = 0; column < dimension; ++column) {
                const Complex term = original_matrix[row][column] * solution[column];
                actual += term;
                scale += std::abs(term);
            }
            requireComplexFinite(actual, "AC residual calculation");
            if (!std::isfinite(scale)) throw SolverError("AC residual scale is outside the finite numeric range");
            const double residual = std::abs(actual - original_rhs[row]);
            requireFinite(residual, "AC equation residual");
            const double normalized = residual / scale;
            requireFinite(normalized, "normalized AC equation residual");
            normalized_residual = std::max(normalized_residual, normalized);
        }
        result.normalized_equation_residuals.push_back(normalized_residual);
        for (std::size_t node = 0; node < circuit.node_count; ++node) {
            if (node == circuit.ground_index) continue;
            const auto value = solution[node_slot[node]];
            const auto output_index = sample * circuit.node_count + node;
            result.node_voltage_real_v[output_index] = value.real();
            result.node_voltage_imag_v[output_index] = value.imag();
        }
    }
    return result;
}

} // namespace circuitmind

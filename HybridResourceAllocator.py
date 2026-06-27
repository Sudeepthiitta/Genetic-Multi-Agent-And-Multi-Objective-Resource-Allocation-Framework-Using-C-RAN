import random
import math
import time
from typing import List, Dict, Tuple


class HybridResourceAllocator:
    def __init__(self, resources: List[str], resource_capacities: Dict[str, float]):
        self.resources = resources
        self.resource_capacities = resource_capacities.copy()
        self.remaining_capacity = resource_capacities.copy()

    # -----------------------------
    # Core allocation for a given order
    # -----------------------------
    def _allocate_in_order(self, ordered_demands: List[Dict], strategy: str = "default") -> Tuple[List[Dict], Dict[str, float]]:
        self.remaining_capacity = self.resource_capacities.copy()

        allocated_demands = []
        for idx, demand in enumerate(ordered_demands):
            allocation = {}
            # Multiobjective-only: keep a soft reserve for upcoming high-priority demands.
            # This helps avoid consuming all resources early on low-priority requests.
            reserve_by_resource = {r: 0.0 for r in self.resources}
            if strategy == "priority_reserve":
                for future in ordered_demands[idx + 1:]:
                    f_pr = float(future.get("priority", 1.0))
                    if f_pr >= 0.8:
                        for r in self.resources:
                            reserve_by_resource[r] += max(0.0, float(future["resource_needs"].get(r, 0.0)))

                # keep only part of future high-priority needs as reserve (soft reserve)
                for r in self.resources:
                    reserve_by_resource[r] = min(self.remaining_capacity[r], reserve_by_resource[r] * 0.35)

            for r, need in demand["resource_needs"].items():
                need = max(0, float(need))
                if strategy == "priority_reserve" and float(demand.get("priority", 1.0)) < 0.8:
                    available = max(0.0, self.remaining_capacity[r] - reserve_by_resource.get(r, 0.0))
                else:
                    available = self.remaining_capacity[r]
                alloc = min(need, available)
                allocation[r] = alloc
                self.remaining_capacity[r] -= alloc

            unfulfilled = {r: max(0, float(demand["resource_needs"][r]) - allocation[r]) for r in demand["resource_needs"]}

            allocated_demands.append({
                "demand": demand,
                "allocation": allocation,
                "unfulfilled": unfulfilled
            })

        return allocated_demands, self.remaining_capacity.copy()

    def _allocate_fixed_strict(self, ordered_demands: List[Dict]) -> Tuple[List[Dict], Dict[str, float]]:
        # Fixed policy: FCFS with all-or-nothing allocation.
        # If a request cannot be fully satisfied at its turn, it receives nothing.
        self.remaining_capacity = self.resource_capacities.copy()
        allocated_demands = []

        for demand in ordered_demands:
            allocation = {}
            can_fully_allocate = True

            for r, need in demand["resource_needs"].items():
                need = max(0.0, float(need))
                if need > self.remaining_capacity[r]:
                    can_fully_allocate = False
                    break

            for r, need in demand["resource_needs"].items():
                need = max(0.0, float(need))
                alloc = need if can_fully_allocate else 0.0
                allocation[r] = alloc
                if can_fully_allocate:
                    self.remaining_capacity[r] -= alloc

            unfulfilled = {
                r: max(0.0, float(demand["resource_needs"].get(r, 0.0)) - allocation[r])
                for r in demand["resource_needs"]
            }
            allocated_demands.append({
                "demand": demand,
                "allocation": allocation,
                "unfulfilled": unfulfilled
            })

        return allocated_demands, self.remaining_capacity.copy()

    def _allocate_multiobjective(self, ordered_demands: List[Dict]) -> Tuple[List[Dict], Dict[str, float]]:
        n = len(ordered_demands)
        if n == 0:
            return [], self.resource_capacities.copy()

        allocations = [{r: 0.0 for r in self.resources} for _ in range(n)]
        remaining_capacity = self.resource_capacities.copy()

        for r in self.resources:
            cap = float(remaining_capacity[r])
            if cap <= 0:
                continue

            needs = [max(0.0, float(d["resource_needs"].get(r, 0.0))) for d in ordered_demands]
            priorities = [float(d.get("priority", 1.0)) for d in ordered_demands]
            rem = cap
            eps = 1e-9
            total_need = sum(needs)
            congestion = (total_need / cap) if cap > eps else 1.0

            # Distribution strategy:
            # - under heavy congestion -> prioritize high-priority users more strongly
            # - under low congestion -> shift toward fairness balancing
            priority_focus = min(0.95, max(0.62, 0.62 + 0.24 * (congestion - 1.0)))
            fairness_focus = 1.0 - priority_focus

            def distribute(indices, budget, score_fn):
                if budget <= eps or len(indices) == 0:
                    return 0.0
                used_total = 0.0
                left = budget
                active = [i for i in indices if (needs[i] - allocations[i][r]) > eps]
                while left > eps and active:
                    scores = {i: max(eps, score_fn(i)) for i in active}
                    ssum = sum(scores.values())
                    if ssum <= eps:
                        break
                    used = 0.0
                    for i in list(active):
                        residual = needs[i] - allocations[i][r]
                        share = left * (scores[i] / ssum)
                        add = min(residual, share)
                        if add > 0:
                            allocations[i][r] += add
                            used += add
                    if used <= eps:
                        i = max(active, key=lambda k: needs[k] - allocations[k][r])
                        add = min(left, max(0.0, needs[i] - allocations[i][r]))
                        if add <= eps:
                            break
                        allocations[i][r] += add
                        used += add
                    left -= used
                    used_total += used
                    active = [i for i in active if (needs[i] - allocations[i][r]) > eps]
                return used_total

            critical = [i for i in range(n) if priorities[i] >= 0.80 and needs[i] > eps]
            high = [i for i in range(n) if priorities[i] >= 0.65 and needs[i] > eps]
            low = [i for i in range(n) if i not in high and needs[i] > eps]

            # Phase 0: guarantee a meaningful base share for critical demands under pressure.
            if critical and congestion > 1.0:
                guarantee_budget = rem * min(0.56, 0.26 + 0.15 * (congestion - 1.0))
                used0 = distribute(
                    critical,
                    guarantee_budget,
                    lambda i: (needs[i] - allocations[i][r]) * (1.0 + 5.8 * (priorities[i] ** 2))
                )
                rem = max(0.0, rem - used0)

            # Phase 1: protect high-priority demands first.
            if high:
                phase1_ratio = min(0.98, max(0.72, 0.72 + 0.28 * (congestion - 1.0)))
                phase1 = rem * (phase1_ratio if low else 1.0)
                used1 = distribute(
                    high,
                    phase1,
                    lambda i: (needs[i] - allocations[i][r]) * (1.0 + 4.2 * priorities[i] + 2.8 * (priorities[i] ** 2))
                )
                rem = max(0.0, rem - used1)

            # Phase 2: fair balancing across all remaining deficits.
            all_idx = [i for i in range(n) if needs[i] > eps]
            used2 = distribute(
                all_idx,
                rem,
                lambda i: (needs[i] - allocations[i][r]) * (
                    priority_focus * (0.18 + 0.82 * priorities[i]) +
                    fairness_focus * (1.0 - (allocations[i][r] / needs[i] if needs[i] > eps else 1.0))
                ) * (
                    1.0 +
                    0.82 * (1.0 - (allocations[i][r] / needs[i] if needs[i] > eps else 1.0)) +
                    0.36 * ((needs[i] / cap) if cap > eps else 0.0) +
                    0.28 * priorities[i]
                )
            )
            rem = max(0.0, rem - used2)

            # Phase 3: spend any tiny leftover on the most valuable remaining deficits.
            if rem > eps:
                used3 = distribute(
                    all_idx,
                    rem,
                    lambda i: (needs[i] - allocations[i][r]) * (1.0 + 5.0 * priorities[i])
                )
                rem = max(0.0, rem - used3)
            remaining_capacity[r] = rem

        allocations = self._priority_rebalance(ordered_demands, allocations)
        remaining_capacity = self._compute_remaining_capacity_from_allocations(ordered_demands, allocations)

        allocated_demands = []
        for i, demand in enumerate(ordered_demands):
            allocation = {r: allocations[i][r] for r in self.resources}
            unfulfilled = {
                r: max(0.0, float(demand["resource_needs"].get(r, 0.0)) - allocation[r])
                for r in self.resources
            }
            allocated_demands.append({
                "demand": demand,
                "allocation": allocation,
                "unfulfilled": unfulfilled
            })

        return allocated_demands, remaining_capacity

    def _compute_remaining_capacity_from_allocations(self, ordered_demands: List[Dict], allocations: List[Dict[str, float]]) -> Dict[str, float]:
        remaining = self.resource_capacities.copy()
        for r in self.resources:
            used = sum(float(row.get(r, 0.0)) for row in allocations)
            remaining[r] = max(0.0, float(self.resource_capacities.get(r, 0.0)) - used)
        return remaining

    def _priority_rebalance(self, ordered_demands: List[Dict], allocations: List[Dict[str, float]]) -> List[Dict[str, float]]:
        n = len(ordered_demands)
        if n <= 1:
            return allocations

        adjusted = [{r: float(allocations[i].get(r, 0.0)) for r in self.resources} for i in range(n)]
        priorities = [float(d.get("priority", 1.0)) for d in ordered_demands]

        for r in self.resources:
            needs = [max(0.0, float(d["resource_needs"].get(r, 0.0))) for d in ordered_demands]
            deficits = [max(0.0, needs[i] - adjusted[i][r]) for i in range(n)]

            high_needers = sorted(
                [i for i in range(n) if deficits[i] > 1e-9 and priorities[i] >= 0.65],
                key=lambda i: (-priorities[i], -deficits[i], i)
            )
            low_donors = sorted(
                [i for i in range(n) if adjusted[i][r] > 1e-9],
                key=lambda i: (priorities[i], adjusted[i][r], i)
            )

            for hi in high_needers:
                if deficits[hi] <= 1e-9:
                    continue
                for lo in low_donors:
                    if lo == hi:
                        continue
                    if priorities[lo] >= priorities[hi] - 0.08:
                        continue

                    lo_need = needs[lo]
                    lo_alloc = adjusted[lo][r]
                    if lo_need <= 1e-9 or lo_alloc <= 1e-9:
                        continue

                    lo_satisfaction = lo_alloc / lo_need if lo_need > 1e-9 else 1.0
                    transferable = lo_alloc - (lo_need * 0.55)
                    if priorities[lo] < 0.45:
                        transferable = lo_alloc - (lo_need * 0.35)
                    elif priorities[lo] < 0.6:
                        transferable = lo_alloc - (lo_need * 0.45)

                    if lo_satisfaction < 0.72:
                        transferable = min(transferable, lo_alloc - (lo_need * 0.65))

                    transferable = max(0.0, transferable)
                    if transferable <= 1e-9:
                        continue

                    shift = min(deficits[hi], transferable)
                    if shift <= 1e-9:
                        continue

                    adjusted[lo][r] -= shift
                    adjusted[hi][r] += shift
                    deficits[hi] -= shift
                    if deficits[hi] <= 1e-9:
                        break

        return adjusted

    # -----------------------------
    # Cost / fitness (lower is better)
    # -----------------------------
    @staticmethod
    def _jains_fairness(values: List[float]) -> float:
        vals = [max(0.0, float(v)) for v in values]
        if len(vals) == 0:
            return 0.0
        s = sum(vals)
        sq = sum(v * v for v in vals)
        if sq <= 0:
            return 0.0
        return (s * s) / (len(vals) * sq)

    def evaluate_order_cost(self, ordered_demands: List[Dict]) -> float:
        # simulate using same multiobjective allocator that genetic optimizes for
        simulated, remaining = self._allocate_multiobjective(ordered_demands)
        total_unfulfilled = 0.0
        weighted_shortfall = 0.0
        total_waste = 0.0
        per_request_satisfaction = []
        resource_used = {r: 0.0 for r in self.resources}
        high_priority_unfulfilled = 0.0
        high_priority_shortfall = 0.0
        unsatisfied_high_count = 0.0

        for row in simulated:
            d = row["demand"]
            pr = float(d.get("priority", 1.0))
            req_sum = 0.0
            alloc_sum = 0.0
            un_sum = 0.0
            for r in self.resources:
                need = max(0.0, float(d["resource_needs"].get(r, 0.0)))
                alloc = max(0.0, float(row["allocation"].get(r, 0.0)))
                un = max(0.0, float(row["unfulfilled"].get(r, 0.0)))
                req_sum += need
                alloc_sum += alloc
                un_sum += un
                resource_used[r] += alloc

            total_unfulfilled += un_sum
            if req_sum > 0:
                weighted_shortfall += (un_sum / req_sum) * (1.0 + 2.5 * pr)
                satisfaction = (alloc_sum / req_sum)
            else:
                satisfaction = 1.0

            if pr >= 0.8:
                high_priority_unfulfilled += un_sum * (1.0 + 4.8 * pr)
                high_priority_shortfall += (1.0 - satisfaction) * (1.0 + 3.6 * pr)
                if satisfaction < 0.999:
                    unsatisfied_high_count += 1.6 + (1.4 * pr)
            elif pr >= 0.65:
                high_priority_unfulfilled += un_sum * (0.55 + 2.8 * pr)
                high_priority_shortfall += (1.0 - satisfaction) * (0.45 + 1.7 * pr)

            per_request_satisfaction.append(satisfaction)

        for r in self.resources:
            total_waste += max(0, remaining[r])

        fairness = self._jains_fairness(per_request_satisfaction)

        util_rates = []
        for r in self.resources:
            cap = float(self.resource_capacities.get(r, 0.0))
            util_rates.append((resource_used[r] / cap) if cap > 0 else 0.0)

        mean_util = (sum(util_rates) / len(util_rates)) if len(util_rates) > 0 else 0.0
        std_util = math.sqrt(sum((u - mean_util) ** 2 for u in util_rates) / len(util_rates)) if len(util_rates) > 0 else 0.0
        cv = (std_util / mean_util) if mean_util > 1e-9 else 1.0

        # Fulfillment-first fitness with strong pressure to satisfy high-priority requests.
        high_priority_term = high_priority_unfulfilled * 620.0
        high_priority_count_term = unsatisfied_high_count * 260.0
        total_unfulfilled_term = total_unfulfilled * 18.0
        shortfall_term = weighted_shortfall * 320.0
        high_priority_shortfall_term = high_priority_shortfall * 300.0
        waste_term = total_waste * 1.0
        fairness_penalty = (1.0 - fairness) * 60.0
        balance_penalty = cv * 18.0

        return (
            high_priority_term +
            high_priority_count_term +
            total_unfulfilled_term +
            shortfall_term +
            high_priority_shortfall_term +
            waste_term +
            fairness_penalty +
            balance_penalty
        )

    # -----------------------------
    # Ordering strategies
    # -----------------------------
    def _order_fixed(self, demands: List[Dict]) -> List[Dict]:
        # FIXED: keep user submission order (FCFS)
        return list(demands)

    def _order_hybrid(self, demands: List[Dict]) -> List[Dict]:
        # HYBRID: strict priority-first order (highest priority first).
        # Keep original insertion order for equal priority values.
        return sorted(demands, key=lambda d: -float(d.get("priority", 0.0)))

    def _order_genetic(self, demands: List[Dict], generations=None, pop_size=None, seed=None) -> List[Dict]:
        n = len(demands)
        if n <= 1:
            return list(demands)

        # Keep runtime responsive for web requests.
        generations = generations if generations is not None else max(54, n * 18)
        pop_size = pop_size if pop_size is not None else max(30, n * 9)
        pop_size = max(18, min(pop_size, 120))
        time_budget_sec = 4.0
        start_time = time.perf_counter()
        rng_seed = seed if seed is not None else random.randrange(1, 10**6)
        rng = random.Random(rng_seed)

        # Make population as permutations of indices
        base = list(range(n))
        id_to_index = {id(d): i for i, d in enumerate(demands)}

        def demand_list_to_perm(ordered):
            return [id_to_index[id(d)] for d in ordered]

        population = []

        def hybrid_density_order():
            return sorted(
                base,
                key=lambda idx: (
                    -float(demands[idx].get("priority", 0.0)),
                    -sum(float(demands[idx]["resource_needs"].get(r, 0.0)) for r in self.resources)
                )
            )

        def weighted_efficiency_order():
            def score_idx(idx):
                priority = float(demands[idx].get("priority", 0.0))
                total_need = sum(float(demands[idx]["resource_needs"].get(r, 0.0)) for r in self.resources)
                return (priority + 0.15) / max(1.0, total_need)
            return sorted(base, key=lambda idx: (-score_idx(idx), -float(demands[idx].get("priority", 0.0))))

        # Inject strong baselines so GA starts from practical solutions.
        population.append(base[:])  # fixed order
        population.append(list(reversed(base[:])))  # reverse order
        population.append(demand_list_to_perm(self._order_hybrid(demands)))  # hybrid baseline
        population.append(hybrid_density_order())
        population.append(weighted_efficiency_order())

        for _ in range(pop_size):
            perm = base[:]
            rng.shuffle(perm)
            population.append(perm)
        population = population[:pop_size]

        def perm_to_demands(perm):
            return [demands[i] for i in perm]

        def score(perm):
            return self.evaluate_order_cost(perm_to_demands(perm))

        def crossover_ox(a, b):
            # order crossover (OX)
            n = len(a)
            i, j = sorted(rng.sample(range(n), 2))
            child = [None] * n
            child[i:j] = a[i:j]
            fill = [x for x in b if x not in child[i:j]]
            k = 0
            for idx in range(n):
                if child[idx] is None:
                    child[idx] = fill[k]
                    k += 1
            return child

        def mutate(p, rate=0.55):
            if len(p) < 2 or rng.random() >= rate:
                return p
            kind = rng.choice(["swap", "insert", "reverse"])
            i, j = sorted(rng.sample(range(len(p)), 2))
            if kind == "swap":
                p[i], p[j] = p[j], p[i]
            elif kind == "insert":
                v = p.pop(j)
                p.insert(i, v)
            else:  # reverse segment
                p[i:j + 1] = reversed(p[i:j + 1])
            return p

        def tournament(pop, k=5):
            cands = rng.sample(pop, min(k, len(pop)))
            return min(cands, key=score)

        def local_search(perm, tries=4):
            # Small hill-climb improves elite quickly with mixed move types.
            best = perm[:]
            best_score = score(best)
            for _ in range(tries):
                cand = best[:]
                i, j = sorted(rng.sample(range(len(best)), 2))
                move = rng.choice(["swap", "insert", "reverse"])
                if move == "swap":
                    cand[i], cand[j] = cand[j], cand[i]
                elif move == "insert":
                    value = cand.pop(j)
                    cand.insert(i, value)
                else:
                    cand[i:j + 1] = reversed(cand[i:j + 1])
                cand_score = score(cand)
                if cand_score < best_score:
                    best = cand
                    best_score = cand_score
            return best

        stagnation = 0
        best_overall = min(population, key=score)
        best_overall_score = score(best_overall)

        for _ in range(generations):
            if time.perf_counter() - start_time > time_budget_sec:
                break
            population.sort(key=score)
            elite_count = max(4, pop_size // 5)
            elite = [p[:] for p in population[:elite_count]]

            # Memetic refinement for top candidates.
            elite = [local_search(p, tries=14) for p in elite]

            new_pop = elite[:]
            adaptive_mutation = 0.34 if stagnation < 6 else 0.78
            while len(new_pop) < pop_size:
                parent1 = tournament(population, k=5)
                parent2 = tournament(population, k=6)
                child = crossover_ox(parent1, parent2)
                child = mutate(child, rate=adaptive_mutation)
                new_pop.append(child)

            # Random immigrants help escape local minima.
            for _i in range(max(1, pop_size // 12)):
                perm = base[:]
                rng.shuffle(perm)
                new_pop[-(_i + 1)] = perm

            population = new_pop
            gen_best = min(population, key=score)
            gen_best_score = score(gen_best)
            if gen_best_score + 1e-9 < best_overall_score:
                best_overall = gen_best[:]
                best_overall_score = gen_best_score
                stagnation = 0
            else:
                stagnation += 1

            # If stuck, perturb worst half while keeping elites.
            if stagnation >= 12:
                keep = population[:elite_count]
                refill = []
                while len(keep) + len(refill) < pop_size:
                    perm = base[:]
                    rng.shuffle(perm)
                    refill.append(perm)
                population = keep + refill
                stagnation = 0

        return perm_to_demands(best_overall)

    # -----------------------------
    # Public API
    # -----------------------------
    def allocate_hybrid(self, demands: List[Dict], algorithm_type: str):
        algorithm_type = (algorithm_type or "").lower().strip()

        if algorithm_type == "fixed":
            ordered = self._order_fixed(demands)
            return self._allocate_fixed_strict(ordered)
        elif algorithm_type == "hybrid":
            ordered = self._order_hybrid(demands)
            return self._allocate_in_order(ordered, strategy="default")
        else:
            # Default to multiobjective as it's the most honest and best for overall allocation
            ordered = self._order_genetic(demands)
            return self._allocate_multiobjective(ordered)

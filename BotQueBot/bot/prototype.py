from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

OUTPUT_FILE = Path(__file__).with_name("match_output.txt")

log_file = None


def start_log():
    global log_file
    log_file = open(OUTPUT_FILE, "w", encoding="utf-8")


def log(text=""):
    if log_file is not None:
        log_file.write(str(text) + "\n")


def end_log():
    global log_file
    if log_file:
        log_file.close()
        log_file = None


ROLE_DATA = {
    "tank": {"roles": ["tank"], "tier": 3},
    "dps": {"roles": ["dps"], "tier": 3},
    "sup": {"roles": ["sup"], "tier": 3},

    "dps_tank": {"roles": ["dps", "tank"], "tier": 2},
    "tank_sup": {"roles": ["tank", "sup"], "tier": 2},
    "dps_sup": {"roles": ["dps", "sup"], "tier": 2},

    "flex": {"roles": ["tank", "dps", "sup"], "tier": 1},
}

TEAM_COMPOSITION = {
    "tank": 2,
    "dps": 2,
    "sup": 1,
}

ROLE_FILLED_POINTS = {
    "tank": 20,
    "dps": 20,
    "sup": 10,
}

ROLE_MISSING_PENALTY = {
    "tank": 15,
    "dps": 20,
    "sup": 10,
}

UNASSIGNED_PLAYER_PENALTY = 5
queue = []


@dataclass
class Player:
    name: str
    elo: int
    role_preference: str

    def possible_roles(self):
        return ROLE_DATA[self.role_preference]["roles"]

    def role_tier(self):
        return ROLE_DATA[self.role_preference]["tier"]

    def is_pure_support(self):
        return self.role_preference == "sup" and self.role_tier() == 3


def join_queue(name, elo, role_preference):
    role_preference = role_preference.lower()

    if role_preference not in ROLE_DATA:
        log("Invalid role.")
        return

    if any(p.name == name for p in queue):
        log(f"{name} is already in the queue.")
        return

    queue.append(Player(name, elo, role_preference))
    log(f"{name} joined queue as {role_preference} ({elo} elo).")


def leave_queue(name):
    global queue

    old_size = len(queue)
    queue = [p for p in queue if p.name != name]

    if len(queue) == old_size:
        log(f"{name} wasn't in the queue.")
    else:
        log(f"{name} left the queue.")


def show_queue():
    if not queue:
        log("Queue empty.")
        return

    log("\n=== QUEUE ===")
    for p in queue:
        log(
            f"{p.name} | {p.role_preference} | "
            f"tier {p.role_tier()} | {p.elo} elo"
        )


def team_elo(team):
    return sum(p.elo for p in team)


def average_team_elo(team):
    return team_elo(team) / len(team)


def average_match_elo(team_a, team_b):
    return (
        team_elo(team_a) + team_elo(team_b)
    ) / (
        len(team_a) + len(team_b)
    )


def elo_difference(team_a, team_b):
    return abs(average_team_elo(team_a) - average_team_elo(team_b))


def total_pure_supports(players):
    return sum(1 for p in players if p.is_pure_support())


def count_pure_supports(team):
    return sum(1 for p in team if p.is_pure_support())


def pure_support_split_is_good(team_a, team_b):
    support_a = count_pure_supports(team_a)
    support_b = count_pure_supports(team_b)

    total_supports = support_a + support_b

    if total_supports < 2:
        return True

    return abs(support_a - support_b) <= 1


def score_team_roles(team):
    needed = TEAM_COMPOSITION.copy()
    score = 0
    assigned_roles = {}

    sorted_players = sorted(
        team,
        key=lambda p: p.role_tier(),
        reverse=True
    )

    for player in sorted_players:
        for role in player.possible_roles():
            if needed.get(role, 0) > 0:
                assigned_roles[player.name] = role
                needed[role] -= 1
                score += ROLE_FILLED_POINTS[role]
                break

    missing_roles = sum(needed.values())
    unassigned_players = len(team) - len(assigned_roles)

    for role, amount_missing in needed.items():
        score -= ROLE_MISSING_PENALTY[role] * amount_missing

    score -= unassigned_players * UNASSIGNED_PLAYER_PENALTY

    return score, assigned_roles


def score_match_roles(match):
    team_a_score, team_a_roles = score_team_roles(match["team_a"])
    team_b_score, team_b_roles = score_team_roles(match["team_b"])

    total_score = team_a_score + team_b_score
    role_score_diff = abs(team_a_score - team_b_score)

    return total_score, role_score_diff, team_a_roles, team_b_roles


def find_best_elo_matches(matches):
    if not matches:
        return None, []

    best_elo_diff = min(match["elo_diff"] for match in matches)

    best_elo_matches = [
        match
        for match in matches
        if match["elo_diff"] == best_elo_diff
    ]

    return best_elo_diff, best_elo_matches


def get_scored_matches_in_elo_range(
    valid_matches,
    elo_range,
    reference_best_elo_diff=None
):
    if not valid_matches:
        return [], None, []

    valid_best_elo_diff, valid_best_elo_matches = find_best_elo_matches(
        valid_matches
    )

    if reference_best_elo_diff is None:
        reference_best_elo_diff = valid_best_elo_diff

    elo_filtered_matches = [
        match
        for match in valid_matches
        if match["elo_diff"] <= reference_best_elo_diff + elo_range
    ]

    scored_matches = []

    for index, match in enumerate(elo_filtered_matches, start=1):
        (
            role_score,
            role_score_diff,
            team_a_roles,
            team_b_roles
        ) = score_match_roles(match)

        scored_match = match.copy()
        scored_match["match_id"] = index
        scored_match["role_score"] = role_score
        scored_match["role_score_diff"] = role_score_diff
        scored_match["team_a_roles"] = team_a_roles
        scored_match["team_b_roles"] = team_b_roles

        scored_matches.append(scored_match)

    return scored_matches, valid_best_elo_diff, valid_best_elo_matches


def choose_best_match_from_scored_matches(scored_matches):
    if not scored_matches:
        return None

    best_role_score = max(
        match["role_score"]
        for match in scored_matches
    )

    best_role_matches = [
        match
        for match in scored_matches
        if match["role_score"] == best_role_score
    ]

    best_match = min(
        best_role_matches,
        key=lambda match: (
            match["role_score_diff"],
            match["elo_diff"]
        )
    )

    best_match["best_role_matches"] = best_role_matches

    return best_match


def find_best_match(elo_range=50):
    if len(queue) < 10:
        log("Not enough players. Need 10 players.")
        return None

    players = queue[:10]
    all_matches = []
    valid_matches = []

    solo_support_count = total_pure_supports(players)
    must_split_supports = solo_support_count >= 2

    for team_a_tuple in combinations(players, 5):
        if players[0] not in team_a_tuple:
            continue

        team_a = list(team_a_tuple)
        team_b = [p for p in players if p not in team_a]

        elo_diff = elo_difference(team_a, team_b)

        match_data = {
            "team_a": team_a,
            "team_b": team_b,
            "elo_diff": elo_diff,
            "support_a": count_pure_supports(team_a),
            "support_b": count_pure_supports(team_b),
            "total_pure_supports": solo_support_count,
        }

        all_matches.append(match_data)

        if must_split_supports:
            if not pure_support_split_is_good(team_a, team_b):
                continue

        valid_matches.append(match_data)

    if not valid_matches:
        log("Could not create a valid match.")
        return None

    (
        theoretical_best_elo_diff,
        theoretical_best_elo_matches
    ) = find_best_elo_matches(all_matches)

    (
        scored_matches,
        valid_best_elo_diff,
        valid_best_elo_matches
    ) = get_scored_matches_in_elo_range(
        valid_matches,
        elo_range,
        reference_best_elo_diff=theoretical_best_elo_diff
    )

    best_match = choose_best_match_from_scored_matches(scored_matches)

    if best_match is None:
        log("Could not choose a match.")
        return None

    best_match["theoretical_best_match"] = theoretical_best_elo_matches[0]
    best_match["best_elo_diff"] = theoretical_best_elo_diff
    best_match["valid_best_elo_diff"] = valid_best_elo_diff
    best_match["best_elo_matches"] = theoretical_best_elo_matches
    best_match["valid_best_elo_matches"] = valid_best_elo_matches
    best_match["matches_before_support_filter"] = len(all_matches)
    best_match["matches_after_support_filter"] = len(valid_matches)
    best_match["matches_in_elo_range"] = len(scored_matches)
    best_match["scored_matches_in_elo_range"] = scored_matches

    print_match(best_match)
    return best_match


def print_team(team_name, team, assigned_roles=None):
    log(f"\n{team_name}:")
    log(f"Average Elo: {average_team_elo(team):.1f}")

    role_order = {
        "tank": 0,
        "dps": 1,
        "sup": 2
    }

    if assigned_roles:
        sorted_team = sorted(
            team,
            key=lambda p: role_order.get(
                assigned_roles.get(p.name, "zzz"),
                999
            )
        )
    else:
        sorted_team = team

    for p in sorted_team:
        playing_role = ""

        if assigned_roles and p.name in assigned_roles:
            playing_role = f"| playing: {assigned_roles[p.name]} "

        log(
            f"- {p.name} "
            f"| selected: {p.role_preference} "
            f"{playing_role}"
            f"| tier: {p.role_tier()} "
            f"| elo: {p.elo}"
        )


def print_scored_matches(scored_matches):
    log("\n=== VALID MATCHES INSIDE ELO RANGE ===")

    for index, match in enumerate(scored_matches, start=1):
        team_a = match["team_a"]
        team_b = match["team_b"]

        log(f"\n--- Match {index} ---")
        log(f"Average Elo Team A: {average_team_elo(team_a):.1f}")
        log(f"Average Elo Team B: {average_team_elo(team_b):.1f}")
        log(f"Average Elo Difference: {match['elo_diff']:.1f}")
        log(f"Role Score: {match['role_score']}")
        log(f"Role Score Difference: {match['role_score_diff']}")
        log(f"Pure Supports Team A: {match['support_a']}")
        log(f"Pure Supports Team B: {match['support_b']}")

        print_team("Team A", team_a, match.get("team_a_roles"))
        print_team("Team B", team_b, match.get("team_b_roles"))


def print_match(match):
    team_a = match["team_a"]
    team_b = match["team_b"]

    theoretical_match = match.get("theoretical_best_match")

    log("\n======================")
    log("=== FINAL MATCH FOUND ===")
    log("======================")

    log(f"\nChosen Match ID: {match.get('match_id')}")

    if theoretical_match:
        log(
            f"Theoretical Best Elo Difference: "
            f"{theoretical_match['elo_diff']:.1f}"
        )
        log(
            f"Theoretical Match Avg Elo: "
            f"{average_match_elo(theoretical_match['team_a'], theoretical_match['team_b']):.1f}"
        )
        log(
            f"Theoretical Team A Avg Elo: "
            f"{average_team_elo(theoretical_match['team_a']):.1f}"
        )
        log(
            f"Theoretical Team B Avg Elo: "
            f"{average_team_elo(theoretical_match['team_b']):.1f}"
        )

    log("")

    log(
        f"Final Match Elo Difference: "
        f"{match['elo_diff']:.1f}"
    )
    log(
        f"Final Match Avg Elo: "
        f"{average_match_elo(team_a, team_b):.1f}"
    )
    log(
        f"Final Team A Avg Elo: "
        f"{average_team_elo(team_a):.1f}"
    )
    log(
        f"Final Team B Avg Elo: "
        f"{average_team_elo(team_b):.1f}"
    )

    log("")

    log(
        f"Matches Before Support Filter: "
        f"{match.get('matches_before_support_filter')}"
    )
    log(
        f"Matches After Support Filter: "
        f"{match.get('matches_after_support_filter')}"
    )
    log(
        f"Matches Inside Elo Range: "
        f"{match.get('matches_in_elo_range')}"
    )

    if "best_role_matches" in match:
        log(
            f"Matches With Best Role Score: "
            f"{len(match['best_role_matches'])}"
        )

    log(f"Chosen Match Role Score: {match.get('role_score')}")
    log(
        f"Chosen Match Role Score Difference: "
        f"{match.get('role_score_diff')}"
    )

    log("")

    log(f"Pure Supports Team A: {match['support_a']}")
    log(f"Pure Supports Team B: {match['support_b']}")

    print_team("Team A", team_a, match.get("team_a_roles"))
    print_team("Team B", team_b, match.get("team_b_roles"))

    if "best_role_matches" in match:
        log("\n=== BEST ROLE SCORE MATCHES ===")

        for m in match["best_role_matches"]:
            log(
                f"Match {m.get('match_id')} "
                f"| Avg Elo Diff: {m['elo_diff']:.1f} "
                f"| Role Score: {m['role_score']} "
                f"| Role Diff: {m['role_score_diff']}"
            )

    if "scored_matches_in_elo_range" in match:
        print_scored_matches(match["scored_matches_in_elo_range"])


start_log()
join_queue("PointTank", 2009, "tank")
join_queue("Seven", 2150, "dps")
join_queue("Gonchi", 2203, "dps")
join_queue("Mosticard", 1432, "tank")
join_queue("Yoinko", 1752, "dps")
join_queue("Eagle", 1932, "tank")
join_queue("Km", 1771, "dps")
join_queue("Indi", 1800, "dps")
join_queue("PJ", 1742, "tank")
join_queue("Gio", 1753, "dps")
join_queue("Spirit", 1754, "dps")
join_queue("Gigi", 1472, "sup")
join_queue("Destro", 1638, "flex")
join_queue("Shax", 1697, "dps_sup")
join_queue("Sango", 1548, "sup")
join_queue("Yulwe", 1672, "dps_sup")
join_queue("Foxys", 1855, "dps_tank")
join_queue("Scorchable", 1457, "tank_sup")
join_queue("Fywr", 1941, "flex")

show_queue()
find_best_match(elo_range=15)

end_log()

print(f"Output written to: {OUTPUT_FILE}")

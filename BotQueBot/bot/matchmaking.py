from dataclasses import dataclass
from itertools import combinations
from random import choice


SUPPORT_SPLIT_ELO_RANGE = 7.5
ELO_RANGE_EXPANSION_STEP = 7.5

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

PALADINS_MAPS = [
    {"key": "ascension_peak", "name": "Ascension Peak"},
    {"key": "bazaar", "name": "Bazaar"},
    {"key": "brightmarsh", "name": "Brightmarsh"},
    {"key": "frog_isle", "name": "Frog Isle"},
    {"key": "frozen_guard", "name": "Frozen Guard"},
    {"key": "fish_market", "name": "Fish Market"},
    {"key": "ice_mines", "name": "Ice Mines"},
    {"key": "jaguar_falls", "name": "Jaguar Falls"},
    {"key": "serpent_beach", "name": "Serpent Beach"},
    {"key": "shattered_desert", "name": "Shattered Desert"},
    {"key": "splitstone_quarry", "name": "Splitstone Quarry"},
    {"key": "stone_keep", "name": "Stone Keep"},
    {"key": "timber_mill", "name": "Timber Mill"},
    {"key": "warders_gate", "name": "Warder's Gate"},
    {"key": "dawnforge", "name": "Dawnforge"},
]


@dataclass
class Player:
    name: str
    elo: float
    role_preference: str
    backend_id: int | None = None
    discord_id: str | None = None
    ign: str = ""

    def possible_roles(self):
        return ROLE_DATA[self.role_preference]["roles"]

    def role_tier(self):
        return ROLE_DATA[self.role_preference]["tier"]

    def is_pure_support(self):
        return self.role_preference == "sup" and self.role_tier() == 3


def team_elo(team):
    return sum(player.elo for player in team)


def average_team_elo(team):
    return team_elo(team) / len(team)


def normalize_team_order(match):
    team_1_avg = average_team_elo(match["team_1"])
    team_2_avg = average_team_elo(match["team_2"])

    if team_1_avg <= team_2_avg:
        return match

    match["team_1"], match["team_2"] = match["team_2"], match["team_1"]

    if "support_1" in match and "support_2" in match:
        match["support_1"], match["support_2"] = (
            match["support_2"],
            match["support_1"]
        )

    if "team_1_roles" in match and "team_2_roles" in match:
        match["team_1_roles"], match["team_2_roles"] = (
            match["team_2_roles"],
            match["team_1_roles"]
        )

    return match


def elo_difference(team_1, team_2):
    return abs(average_team_elo(team_1) - average_team_elo(team_2))


def total_pure_supports(players):
    return sum(1 for player in players if player.is_pure_support())


def count_pure_supports(team):
    return sum(1 for player in team if player.is_pure_support())


def pure_support_split_is_good(team_1, team_2):
    support_1 = count_pure_supports(team_1)
    support_2 = count_pure_supports(team_2)
    total_supports = support_1 + support_2

    if total_supports < 2:
        return True

    return abs(support_1 - support_2) <= 1


def score_team_roles(team):
    needed = TEAM_COMPOSITION.copy()
    score = 0
    assigned_roles = {}

    sorted_players = sorted(
        team,
        key=lambda player: player.role_tier(),
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
    team_1_score, team_1_roles = score_team_roles(match["team_1"])
    team_2_score, team_2_roles = score_team_roles(match["team_2"])

    total_score = team_1_score + team_2_score
    role_score_diff = abs(team_1_score - team_2_score)

    return total_score, role_score_diff, team_1_roles, team_2_roles


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
            team_1_roles,
            team_2_roles
        ) = score_match_roles(match)

        scored_match = match.copy()
        scored_match["match_id"] = index
        scored_match["role_score"] = role_score
        scored_match["role_score_diff"] = role_score_diff
        scored_match["team_1_roles"] = team_1_roles
        scored_match["team_2_roles"] = team_2_roles

        scored_matches.append(scored_match)

    return scored_matches, valid_best_elo_diff, valid_best_elo_matches


def get_scored_matches_with_expanding_elo_range(
    valid_matches,
    starting_elo_range,
    reference_best_elo_diff=None
):
    if not valid_matches:
        return [], None, [], starting_elo_range

    widest_needed_range = max(
        match["elo_diff"]
        for match in valid_matches
    )

    if reference_best_elo_diff is not None:
        widest_needed_range -= reference_best_elo_diff

    widest_needed_range = max(starting_elo_range, widest_needed_range)
    current_range = starting_elo_range

    while current_range <= widest_needed_range:
        (
            scored_matches,
            valid_best_elo_diff,
            valid_best_elo_matches
        ) = get_scored_matches_in_elo_range(
            valid_matches,
            current_range,
            reference_best_elo_diff=reference_best_elo_diff
        )

        if scored_matches:
            return (
                scored_matches,
                valid_best_elo_diff,
                valid_best_elo_matches,
                current_range
            )

        current_range += ELO_RANGE_EXPANSION_STEP

    (
        scored_matches,
        valid_best_elo_diff,
        valid_best_elo_matches
    ) = get_scored_matches_in_elo_range(
        valid_matches,
        widest_needed_range,
        reference_best_elo_diff=reference_best_elo_diff
    )

    return (
        scored_matches,
        valid_best_elo_diff,
        valid_best_elo_matches,
        widest_needed_range
    )


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


def find_best_match_for_players(players, elo_range=15):
    if len(players) < 10:
        return None

    players = players[:10]
    all_matches = []
    valid_matches = []

    solo_support_count = total_pure_supports(players)
    must_split_supports = solo_support_count >= 2

    for team_1_tuple in combinations(players, 5):
        if players[0] not in team_1_tuple:
            continue

        team_1 = list(team_1_tuple)
        team_2 = [
            player
            for player in players
            if player not in team_1
        ]

        match_data = {
            "team_1": team_1,
            "team_2": team_2,
            "elo_diff": elo_difference(team_1, team_2),
            "support_1": count_pure_supports(team_1),
            "support_2": count_pure_supports(team_2),
            "total_pure_supports": solo_support_count,
        }

        all_matches.append(match_data)

        if must_split_supports:
            if not pure_support_split_is_good(team_1, team_2):
                continue

        valid_matches.append(match_data)

    if not valid_matches:
        return None

    (
        theoretical_best_elo_diff,
        theoretical_best_elo_matches
    ) = find_best_elo_matches(all_matches)

    starting_elo_range = (
        SUPPORT_SPLIT_ELO_RANGE if must_split_supports else elo_range
    )

    (
        scored_matches,
        valid_best_elo_diff,
        valid_best_elo_matches,
        final_elo_range
    ) = get_scored_matches_with_expanding_elo_range(
        valid_matches,
        starting_elo_range,
        reference_best_elo_diff=theoretical_best_elo_diff
    )

    best_match = choose_best_match_from_scored_matches(scored_matches)

    if best_match is None:
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
    best_match["starting_elo_range"] = starting_elo_range
    best_match["final_elo_range"] = final_elo_range
    best_match["elo_range_expanded"] = final_elo_range > starting_elo_range
    best_match["map"] = choice(PALADINS_MAPS)
    normalize_team_order(best_match)

    return best_match


def player_from_queue_entry(entry):
    role_preference = entry.get("role_preference", "flex").lower()

    if role_preference not in ROLE_DATA:
        role_preference = "flex"

    return Player(
        name=entry["username"],
        elo=float(entry["mmr"]),
        role_preference=role_preference,
        backend_id=entry.get("id"),
        discord_id=entry.get("discord_id"),
        ign=entry.get("ign", "")
    )


def find_best_match_for_queue(current_queue, elo_range=15):
    players = [
        player_from_queue_entry(entry)
        for entry in current_queue
    ]

    return find_best_match_for_players(players, elo_range)

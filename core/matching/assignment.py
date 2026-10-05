"""Maximum-weight one-to-one assignment for album-sized evidence matrices.

Rows are files, columns are release slots. Zero-weight dummy columns permit a
file to remain unmatched. Callers must assign zero to implausible pairs and
reject zero-weight results; this solver never claims a match on its own.
"""

from __future__ import annotations


def assign_max_weight(weights: list[list[int]]) -> list[tuple[int, int]]:
    if not weights:
        return []
    columns = len(weights[0])
    if any(len(row) != columns for row in weights):
        raise ValueError('All assignment rows must have the same width')
    files = len(weights)
    slots = max(files, columns)
    if slots == 0:
        return []
    padded = [row + [0] * (slots - columns) for row in weights]
    u, v = [0] * (files + 1), [0] * (slots + 1)
    owner = [0] * (slots + 1)
    for file_index in range(1, files + 1):
        owner[0] = file_index
        minimum = [float('inf')] * (slots + 1)
        visited = [False] * (slots + 1)
        previous = [0] * (slots + 1)
        column = 0
        while True:
            visited[column] = True
            row = owner[column]
            delta, next_column = float('inf'), 0
            for candidate in range(1, slots + 1):
                if visited[candidate]:
                    continue
                cost = -padded[row - 1][candidate - 1] - u[row] - v[candidate]
                if cost < minimum[candidate]:
                    minimum[candidate] = cost
                    previous[candidate] = column
                if minimum[candidate] < delta:
                    delta, next_column = minimum[candidate], candidate
            for candidate in range(slots + 1):
                if visited[candidate]:
                    u[owner[candidate]] += delta
                    v[candidate] -= delta
                else:
                    minimum[candidate] -= delta
            column = next_column
            if owner[column] == 0:
                break
        while column:
            prior = previous[column]
            owner[column] = owner[prior]
            column = prior
    return [(owner[column] - 1, column - 1)
            for column in range(1, columns + 1)
            if owner[column] and padded[owner[column] - 1][column - 1] > 0]

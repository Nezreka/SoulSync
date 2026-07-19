"""Unit tests for the smart-collection rule compiler (pure, no DB)."""

from __future__ import annotations

import pytest

from core.video.collections.smart_filter import (
    SmartFilterError,
    compile_rules,
    known_fields,
)


def test_and_of_column_rules():
    sql, params = compile_rules(
        {"match": "all", "rules": [
            {"field": "year", "op": "between", "value": [1980, 1989]},
            {"field": "rating", "op": "gte", "value": 7.0},
        ]}, "movie")
    assert " AND " in sql
    assert "movies.year BETWEEN ? AND ?" in sql
    assert "movies.rating >= ?" in sql
    assert params == [1980.0, 1989.0, 7.0]


def test_or_match_uses_or():
    # Two column-only rules so the only boolean joiner is the top-level one
    # (a genre/person EXISTS subquery has its own internal AND).
    sql, _ = compile_rules(
        {"match": "any", "rules": [
            {"field": "year", "op": "is", "value": 1999},
            {"field": "rating", "op": "gte", "value": 8.0},
        ]}, "movie")
    assert " OR " in sql and " AND " not in sql


def test_genre_and_person_use_exists_subqueries():
    sql, params = compile_rules(
        {"rules": [
            {"field": "genre", "op": "in", "value": ["Action", "Sci-Fi"]},
            {"field": "director", "op": "is", "value": "Nolan"},
            {"field": "actor", "op": "in", "value": ["A", "B"]},
        ]}, "movie")
    assert "EXISTS (SELECT 1 FROM movie_genres" in sql
    assert "c.department = ?" in sql and "c.job = ?" in sql
    # genre values, then crew/Director/name, then cast/names
    assert params == ["Action", "Sci-Fi", "crew", "Director", "Nolan", "cast", "A", "B"]


def test_show_uses_show_tables_and_network():
    sql, _ = compile_rules(
        {"rules": [
            {"field": "network", "op": "is", "value": "HBO"},
            {"field": "genre", "op": "in", "value": ["Drama"]},
        ]}, "show")
    # network now resolves through the show_networks link table (multi-valued), not a column
    assert "show_networks" in sql and "networks" in sql
    assert "shows.network" not in sql
    assert "show_genres" in sql and "movie_genres" not in sql


def test_resolution_filter_movie_vs_show_join():
    m, _ = compile_rules({"rules": [{"field": "resolution", "op": "in", "value": ["2160p"]}]}, "movie")
    s, _ = compile_rules({"rules": [{"field": "resolution", "op": "in", "value": ["2160p"]}]}, "show")
    assert "mf.movie_id = movies.id" in m
    assert "JOIN episodes e ON e.id = mf.episode_id" in s and "e.show_id = shows.id" in s


def test_decade_expands_to_year_ranges():
    sql, params = compile_rules({"rules": [{"field": "decade", "op": "in", "value": [1980, 2000]}]}, "movie")
    assert params == [1980, 1989, 2000, 2009]


def test_franchise_movie_only():
    sql, _ = compile_rules({"rules": [{"field": "franchise", "op": "exists"}]}, "movie")
    assert "tmdb_collection_id IS NOT NULL" in sql
    with pytest.raises(SmartFilterError):
        compile_rules({"rules": [{"field": "franchise", "op": "exists"}]}, "show")


def test_values_are_parameterized_never_inlined():
    # An injection-looking value must land in params, never in the SQL text.
    evil = "Action'); DROP TABLE movies;--"
    sql, params = compile_rules({"rules": [{"field": "studio", "op": "is", "value": evil}]}, "movie")
    assert evil not in sql
    assert evil in params


class TestValidation:
    def test_empty_rules_raises(self):
        with pytest.raises(SmartFilterError):
            compile_rules({"rules": []}, "movie")

    def test_unknown_field_raises(self):
        with pytest.raises(SmartFilterError):
            compile_rules({"rules": [{"field": "nope", "op": "is", "value": 1}]}, "movie")

    def test_field_media_mismatch_raises(self):
        with pytest.raises(SmartFilterError):
            compile_rules({"rules": [{"field": "studio", "op": "is", "value": "X"}]}, "show")
        with pytest.raises(SmartFilterError):
            compile_rules({"rules": [{"field": "network", "op": "is", "value": "X"}]}, "movie")

    def test_bad_operator_for_type_raises(self):
        with pytest.raises(SmartFilterError):
            compile_rules({"rules": [{"field": "year", "op": "contains", "value": 5}]}, "movie")

    def test_bad_media_type_raises(self):
        with pytest.raises(SmartFilterError):
            compile_rules({"rules": [{"field": "year", "op": "is", "value": 1}]}, "episode")


def test_known_fields_differ_by_media():
    assert "franchise" in known_fields("movie")
    assert "franchise" not in known_fields("show")
    assert "network" in known_fields("show") and "network" not in known_fields("movie")
    assert "studio" in known_fields("movie") and "studio" not in known_fields("show")

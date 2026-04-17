"""Test SQL injection vulnerabilities are blocked"""

import pytest

from empirica.data.migrations.migration_runner import add_column_if_missing, column_exists


class TestSQLInjection:
    """Test suite for SQL injection prevention"""

    def test_malicious_table_names_blocked(self, temp_db):
        """Verify malicious table names are rejected"""
        cursor = temp_db.cursor()

        malicious_tables = [
            "'; DROP TABLE sessions; --",
            "sessions'; DELETE FROM sessions; --",
            "sessions OR 1=1 --",
            "sessions UNION SELECT * FROM users",
        ]

        for table in malicious_tables:
            with pytest.raises(ValueError, match="Invalid table name"):
                column_exists(cursor, table, "id")

    def test_valid_tables_accepted(self, temp_db):
        """Verify legitimate table names work"""
        cursor = temp_db.cursor()

        # Should not raise
        result = column_exists(cursor, "sessions", "id")
        assert isinstance(result, bool)

    def test_malicious_column_types_blocked(self, temp_db):
        """Verify malicious column types are rejected"""
        cursor = temp_db.cursor()

        malicious_types = [
            "TEXT; DROP TABLE sessions; --",
            "TEXT') VALUES ('hack'); --",
        ]

        for col_type in malicious_types:
            with pytest.raises(ValueError, match="Invalid column type"):
                add_column_if_missing(cursor, "sessions", "test_col", col_type)

    def test_parameterized_queries_used(self, temp_db):
        """Verify parameterized queries prevent injection"""
        cursor = temp_db.cursor()

        # Add test column with quote in name (should be safe with parameterization)
        result = column_exists(cursor, "sessions", "test'column")

        # Should handle safely without SQL error
        assert isinstance(result, bool)

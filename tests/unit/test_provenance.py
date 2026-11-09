import sqlite3
import tempfile
import pytest
from ct_toolkit.provenance.log import ProvenanceLog
from ct_toolkit.core.exceptions import ChainIntegrityError

class TestProvenanceLogWithStatus:
    """provenance/log.py status-based rollback and multi-agent safety."""

    def setup_method(self):
        self._tmp_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp = self._tmp_file.name
        self._tmp_file.close()
        self.log = ProvenanceLog(vault_path=self.tmp)

    def teardown_method(self):
        import os
        # Close the connection to release the file lock on Windows
        if self.log and self.log._conn:
            self.log._conn.close()
        if os.path.exists(self.tmp):
            os.remove(self.tmp)
        key_path = self.log._vault_path.parent / ".ct_hmac_key"
        if os.path.exists(key_path):
             os.remove(key_path)

    # -- Recording & Status ----------------------------------------------------

    def test_record_sets_status_to_active(self):
        entry_id = self.log.record("q", "a")
        entry = self.log.get_entry(entry_id)
        assert entry.status == "active"

    def test_get_entries_returns_only_active_by_default(self):
        self.log.record("q1", "a1")
        self.log.record("q2", "a2")
        conn = sqlite3.connect(self.tmp)
        conn.execute("UPDATE provenance SET status = 'rolled_back' WHERE request_hash = ?", (self.log._hash_text("q1"),))
        conn.commit()
        conn.close()
        
        active_entries = self.log.get_entries()
        assert len(active_entries) == 1
        assert active_entries[0].request_hash == self.log._hash_text("q2")

    def test_get_entries_can_include_rolled_back(self):
        self.log.record("q1", "a1")
        self.log.record("q2", "a2")
        conn = sqlite3.connect(self.tmp)
        conn.execute("UPDATE provenance SET status = 'rolled_back' WHERE request_hash = ?", (self.log._hash_text("q1"),))
        conn.commit()
        conn.close()

        all_entries = self.log.get_entries(include_rolled_back=True)
        assert len(all_entries) == 2

    # -- Hash chain with status ------------------------------------------------

    def test_verify_chain_ignores_rolled_back_entries(self):
        self.log.record("q1", "a1")
        id2 = self.log.record("q2", "a2") # This will be rolled back
        self.log.record("q3", "a3")

        # Manually mark the second entry as rolled_back
        conn = sqlite3.connect(self.tmp)
        conn.execute("UPDATE provenance SET status = 'rolled_back' WHERE id = ?", (id2,))
        conn.commit()
        conn.close()

        # The chain should now be invalid because q3 points to q2, which is rolled back
        with pytest.raises(ChainIntegrityError):
            self.log.verify_chain()

    def test_get_last_entry_hash_ignores_rolled_back(self):
        id1 = self.log.record("q1", "a1")
        entry1_hash = self.log.get_entry(id1).content_hash()
        
        id2 = self.log.record("q2", "a2")
        # Manually mark the second entry as rolled_back
        conn = sqlite3.connect(self.tmp)
        conn.execute("UPDATE provenance SET status = 'rolled_back' WHERE id = ?", (id2,))
        conn.commit()
        conn.close()

        _, last_hash = self.log._read_chain_tip()
        assert last_hash == entry1_hash

    # -- Safe Rollback ---------------------------------------------------------

    def test_rollback_marks_entries_as_rolled_back(self):
        id1 = self.log.record("q1", "a1", metadata={"agent_id": "agent_A"})
        id2 = self.log.record("q2", "a2", metadata={"agent_id": "agent_A"})

        self.log.rollback("agent_A", id1)

        entry2 = self.log.get_entry(id2)
        assert entry2.status == "rolled_back"

    def test_rollback_is_agent_specific(self):
        self.log.record("q_A1", "a_A1", metadata={"agent_id": "agent_A"})
        id_A2 = self.log.record("q_A2", "a_A2", metadata={"agent_id": "agent_A"})
        id_B1 = self.log.record("q_B1", "a_B1", metadata={"agent_id": "agent_B"})

        self.log.rollback("agent_A", id_A2) # Rollback A's last entry

        # This test is tricky because rollback affects entries *after* the target.
        # Let's add another entry for agent A and then rollback
        self.log._conn.execute("UPDATE provenance SET status = 'active'")
        self.log._conn.commit()

        id_A1 = self.log.get_entries(include_rolled_back=True)[0].id
        self.log.rollback("agent_A", id_A1)

        entry_A2_after_rollback = self.log.get_entry(id_A2)
        entry_B1_after_rollback = self.log.get_entry(id_B1)

        assert entry_A2_after_rollback.status == "rolled_back"
        assert entry_B1_after_rollback.status == "active"

    def test_adding_entry_after_rollback(self):
        id_A1 = self.log.record("q_A1", "a_A1", metadata={"agent_id": "agent_A"})
        hash_A1 = self.log.get_entry(id_A1).content_hash()
        self.log.record("q_A2", "a_A2", metadata={"agent_id": "agent_A"})

        self.log.rollback("agent_A", id_A1)

        id_A3 = self.log.record("q_A3", "a_A3", metadata={"agent_id": "agent_A"})
        entry_A3 = self.log.get_entry(id_A3)

        assert entry_A3.prev_entry_hash == hash_A1
        assert self.log.verify_chain() is True

    def test_rollback_raises_error_for_wrong_agent(self):
        id_A1 = self.log.record("q_A1", "a_A1", metadata={"agent_id": "agent_A"})
        with pytest.raises(ValueError, match="Entry does not belong to the specified agent"):
            self.log.rollback("agent_B", id_A1)

    # -- Read-only access ------------------------------------------------------

    def test_read_only_connection_prohibits_write(self):
        self.log.record("q", "a")  # Ensure db is not empty
        ro_conn = self.log.get_read_only_connection()
        with pytest.raises(sqlite3.OperationalError, match="attempt to write a readonly database"):
            ro_conn.execute("DELETE FROM provenance")
        ro_conn.close()

    def test_new_db_init_with_status_column(self):
        """Ensure a fresh db has the status column."""
        conn = sqlite3.connect(self.tmp)
        cursor = conn.execute("PRAGMA table_info(provenance)")
        columns = [row[1] for row in cursor.fetchall()]
        conn.close()
        assert "status" in columns

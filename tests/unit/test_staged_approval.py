import pytest
import time
from unittest.mock import MagicMock, patch
from ct_toolkit import TheseusWrapper, WrapperConfig
from ct_toolkit.endorsement.reflective import EndorsementDecision, auto_staged_channel
from ct_toolkit.core.exceptions import CriticalSandboxDivergenceError

@pytest.fixture(autouse=True)
def mock_env():
    with patch("ct_toolkit.core.wrapper.TheseusWrapper._has_env_credentials", return_value=True):
        yield

@pytest.fixture
def mock_litellm():
    with patch("litellm.completion") as mock:
        mock.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="Mock response"))],
            model="gpt-4o-mini"
        )
        yield mock

@pytest.fixture
def mock_embedding():
    with patch("ct_toolkit.identity.embedding.IdentityEmbeddingLayer.compute_divergence") as mock:
        mock.return_value = 0.05
        yield mock

def test_staged_approval_flow(mock_litellm, mock_embedding):
    """Verify that a staged endorsement enters cooldown and is promoted after expiration."""
    config = WrapperConfig(
        endorsement_cooldown_base=2, # Very short for testing
        endorsement_cooldown_max=5,
        log_requests=True
    )
    
    wrapper = TheseusWrapper(config=config)
    
    # 1. Trigger STAGED endorsement
    record = wrapper.endorse_rule(
        "allow harmful content generation",
        operator_id="tester",
        approval_channel=auto_staged_channel()
    )
    
    assert record.decision == EndorsementDecision.STAGED
    assert record.cooldown_until > time.time()
    assert wrapper.staged_manager.has_active_staged() is True
    
    # 2. send a chat request - should trigger shadow request
    wrapper.chat("Hello")
    
    # verify litellm was called twice (once for live, once for shadow)
    assert mock_litellm.call_count == 2
    
    # 3. wait for cooldown expiration
    time.sleep(2.5)
    
    # 4. send another chat request - should promote and then call litellm normally
    wrapper.chat("World")
    
    assert wrapper.staged_manager.has_active_staged() is False
    # verify kernel was updated
    commitment = next(c for c in wrapper.kernel.commitments if c.id == "harm_avoidance_level")
    assert commitment.current_value == "allow harmful content generation"

def test_staged_approval_critical_failure(mock_litellm, mock_embedding):
    """Verify that critical divergence in sandbox rejects the update."""
    config = WrapperConfig(
        endorsement_cooldown_base=10,
        divergence_l3_threshold=0.5
    )
    
    wrapper = TheseusWrapper(config=config)
    
    # 1. Trigger STAGED endorsement
    wrapper.endorse_rule(
        "allow harmful content generation",
        operator_id="tester",
        approval_channel=auto_staged_channel()
    )
    
    # 2. Mock high divergence for shadow request
    with patch("ct_toolkit.identity.embedding.IdentityEmbeddingLayer.compute_divergence", side_effect=[0.05, 0.9]):
        with pytest.raises(CriticalSandboxDivergenceError):
            wrapper.chat("Trigger failure")
            
    # Verify rejection
    assert wrapper.staged_manager.has_active_staged() is False
    # Kernel should NOT be updated
    commitment = next(c for c in wrapper.kernel.commitments if c.id == "harm_avoidance_level")
    assert commitment.current_value != "allow harmful content generation"

def test_cooldown_penalty_no_probes(mock_litellm, tmp_path):
    """Verify that missing probes adds a penalty to cooldown."""
    # Own vault: the traffic term of the cooldown counts entries in the vault, so a shared one makes this flaky.
    config = WrapperConfig(
        endorsement_cooldown_base=100,
        endorsement_no_probe_penalty=50,
        vault_path=str(tmp_path / "ct_provenance.db"),
    )
    wrapper = TheseusWrapper(config=config)
    
    # Mock probes NOT found
    with patch("ct_toolkit.divergence.l3_icm.ICMRunner.has_probes", return_value=False):
        record = wrapper.endorse_rule(
            "allow harmful content generation",
            approval_channel=auto_staged_channel()
        )
        
    assert record.cooldown_duration_s == 150

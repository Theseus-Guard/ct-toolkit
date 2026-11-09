from __future__ import annotations

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
except ImportError as e:  # pragma: no cover - exercised only without the ml extra
    raise ImportError(
        "The divergence penalty loss needs torch. Install it with: pip install 'ct-toolkit[ml]'"
    ) from e

class DivergencePenaltyLoss(nn.Module):
    """
    Implements the Divergence Penalty as defined in Section 5.1 of the research paper.
    
    This loss function penalizes normative drift by calculating the distance between
    the model's generated embeddings and a reference 'Kernel' embedding.
    """
    def __init__(self, alpha: float = 1.0, distance_metric: str = "cosine"):
        super().__init__()
        self.alpha = alpha
        self.distance_metric = distance_metric

    def forward(
        self, 
        current_embeddings: torch.Tensor, 
        reference_embeddings: torch.Tensor
    ) -> torch.Tensor:
        """
        Calculates the identity-divergence penalty.
        
        Args:
            current_embeddings: The hidden states or outputs of the model being trained.
            reference_embeddings: The frozen CIK (Constitutional Identity Kernel) embeddings.
            
        Returns:
            A scalar tensor representing the penalty.
        """
        if self.distance_metric == "cosine":
            # 1 - cosine_similarity (range: 0 to 2, where 0 is identical)
            similarity = F.cosine_similarity(current_embeddings, reference_embeddings, dim=-1)
            dist = 1.0 - similarity
        else:
            # Fallback to MSE / Euclidean distance
            dist = F.mse_loss(current_embeddings, reference_embeddings, reduction='none')
            if dist.dim() > 1:
                dist = dist.mean(dim=-1)
            
        # Penalize drift beyond a certain magnitude
        penalty = self.alpha * torch.mean(dist)
        
        return penalty

def compute_alignment_loss(
    model_output_embeddings: torch.Tensor,
    kernel_anchor_embeddings: torch.Tensor,
    alpha: float = 0.5
) -> torch.Tensor:
    """
    Helper function to compute identity alignment loss during training.
    """
    criterion = DivergencePenaltyLoss(alpha=alpha)
    return criterion(model_output_embeddings, kernel_anchor_embeddings)

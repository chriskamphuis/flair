import inspect
import logging
from abc import abstractmethod
from typing import Dict, Generic, List, Sequence, Union

import torch
from torch.nn import Parameter, ParameterList

import flair
from flair.data import DT

log = logging.getLogger("flair")


class Embeddings(torch.nn.Module, Generic[DT]):
    """Abstract base class for all embeddings. Every new type of embedding must implement these methods."""

    def __init__(self):
        """Set some attributes that would otherwise result in errors. Overwrite these in your embedding class."""
        if not hasattr(self, "name"):
            self.name: str = "unnamed_embedding"
        if not hasattr(self, "static_embeddings"):
            # if the embeddings for a sentence are the same in each epoch, set this to True for improved efficiency
            self.static_embeddings = False
        super().__init__()

    @property
    @abstractmethod
    def embedding_length(self) -> int:
        """Returns the length of the embedding vector."""
        raise NotImplementedError

    @property
    @abstractmethod
    def embedding_type(self) -> str:
        raise NotImplementedError

    def embed(self, data_points: Union[DT, List[DT]]) -> List[DT]:
        """Add embeddings to all words in a list of sentences. If embeddings are already added, updates only if embeddings
        are non-static."""

        # if only one sentence is passed, convert to list of sentence
        if not isinstance(data_points, list):
            data_points = [data_points]

        if not self._everything_embedded(data_points) or not self.static_embeddings:
            self._add_embeddings_internal(data_points)

        return data_points

    def _everything_embedded(self, data_points: Sequence[DT]) -> bool:
        for data_point in data_points:
            if self.name not in data_point._embeddings.keys():
                return False
        return True

    @abstractmethod
    def _add_embeddings_internal(self, sentences: List[DT]):
        """Private method for adding embeddings to all words in a list of sentences."""
        pass

    def get_names(self) -> List[str]:
        """Returns a list of embedding names. In most cases, it is just a list with one item, namely the name of
        this embedding. But in some cases, the embedding is made up by different embeddings (StackedEmbedding).
        Then, the list contains the names of all embeddings in the stack."""
        return [self.name]

    def get_named_embeddings_dict(self) -> Dict:
        return {self.name: self}

    @staticmethod
    def get_instance_parameters(locals: dict) -> dict:
        class_definition = locals.get("__class__")
        instance_parameter_names = set(inspect.signature(class_definition.__init__).parameters)  # type: ignore
        instance_parameter_names.remove("self")
        instance_parameter_names.add("__class__")
        instance_parameters = {
            class_attribute: attribute_value
            for class_attribute, attribute_value in locals.items()
            if class_attribute in instance_parameter_names
        }
        return instance_parameters


class ScalarMix(torch.nn.Module):
    """
    Computes a parameterised scalar mixture of N tensors.
    This method was proposed by Liu et al. (2019) in the paper:
    "Linguistic Knowledge and Transferability of Contextual Representations" (https://arxiv.org/abs/1903.08855)

    The implementation is copied and slightly modified from the allennlp repository and is licensed under Apache 2.0.
    It can be found under:
    https://github.com/allenai/allennlp/blob/master/allennlp/modules/scalar_mix.py.
    """

    def __init__(self, mixture_size: int, trainable: bool = False) -> None:
        """
        Inits scalar mix implementation.
        ``mixture = gamma * sum(s_k * tensor_k)`` where ``s = softmax(w)``, with ``w`` and ``gamma`` scalar parameters.
        :param mixture_size: size of mixtures (usually the number of layers)
        """
        super(ScalarMix, self).__init__()
        self.mixture_size = mixture_size

        initial_scalar_parameters = [0.0] * mixture_size

        self.scalar_parameters = ParameterList(
            [
                Parameter(
                    torch.tensor(
                        [initial_scalar_parameters[i]],
                        dtype=torch.float,
                        device=flair.device,
                    ),
                    requires_grad=trainable,
                )
                for i in range(mixture_size)
            ]
        )
        self.gamma = Parameter(
            torch.tensor(
                [1.0],
                dtype=torch.float,
                device=flair.device,
            ),
            requires_grad=trainable,
        )

    def forward(self, tensors: List[torch.Tensor]) -> torch.Tensor:
        """
        Computes a weighted average of the ``tensors``.  The input tensors an be any shape
        with at least two dimensions, but must all be the same shape.
        :param tensors: list of input tensors
        :return: computed weighted average of input tensors
        """
        if len(tensors) != self.mixture_size:
            log.error(
                "{} tensors were passed, but the module was initialized to mix {} tensors.".format(
                    len(tensors), self.mixture_size
                )
            )

        normed_weights = torch.nn.functional.softmax(
            torch.cat([parameter for parameter in self.scalar_parameters]), dim=0
        )
        normed_weights = torch.split(normed_weights, split_size_or_sections=1)

        pieces = []
        for weight, tensor in zip(normed_weights, tensors):
            pieces.append(weight * tensor)
        return self.gamma * sum(pieces)

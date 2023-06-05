# This code is part of Qiskit.
#
# (C) Copyright IBM 2023.
#
# This code is licensed under the Apache License, Version 2.0. You may
# obtain a copy of this license in the LICENSE.txt file in the root directory
# of this source tree or at http://www.apache.org/licenses/LICENSE-2.0.
#
# Any modifications or derivative works of this code must retain this
# copyright notice, and modified files need to carry a notice indicating
# that they have been altered from the originals.

"""A PySCF-based GroundStateSolver for Qiskit Nature."""

from __future__ import annotations

import logging

import numpy as np
from pyscf import fci
from qiskit.quantum_info import SparsePauliOp
from qiskit_nature.second_q.algorithms import GroundStateSolver
from qiskit_nature.second_q.operators import SparseLabelOp
from qiskit_nature.second_q.operators.tensor_ordering import IndexType, to_chemist_ordering
from qiskit_nature.second_q.problems import (
    BaseProblem,
    ElectronicStructureProblem,
    ElectronicStructureResult,
)
from qiskit_nature.second_q.properties import ElectronicDensity

LOGGER = logging.getLogger(__name__)


class PySCFGroundStateSolver(GroundStateSolver):
    """A PySCF-based GroundStateSolver for Qiskit Nature.

    This class provides a ``GroundStateSolver`` for Qiskit Nature, leveraging the ``fci`` module of
    PySCF. This is utility does not enable any Quantum algorithms to be used (since it replaces them
    in the Qiskit workflow) but instead provides a useful utility for debugging classical
    computational workflows based on Qiskit Nature.
    More importantly, it provides a more efficient implementation of what Qiskit otherwise achieves
    using a ``NumPyMinimumEigensolver`` in combination with a ``filter_criterion``. For non-singlet
    spin ground states the setup using Qiskit components is a lot more involved, whereas this class
    provides an easy-to-use alternative.

    Here is an example use cas:

    .. code-block:: python

       from pyscf import fci

       from qiskit_nature.second_q.drivers import MethodType, PySCFDriver
       from qiskit_nature.second_q.transformers import ActiveSpaceTransformer

       from qiskit_nature_pyscf import PySCFGroundStateSolver

       driver = PySCFDriver(
           atom="O 0.0 0.0 0.0; O 0.0 0.0 1.5",
           basis="sto3g",
           spin=2,
           method=MethodType.UHF,
       )
       problem = driver.run()

       transformer = ActiveSpaceTransformer(4, 4)

       problem = transformer.transform(problem)

       solver = PySCFGroundStateSolver(fci.direct_uhf.FCI())

       result = solver.solve(problem)
       print(result)

    For more details please to the documentation of [PySCF](https://pyscf.org/) and
    [Qiskit Nature](https://qiskit.org/documentation/nature/).
    """

    def __init__(self, solver: fci.direct_spin1.FCISolver) -> None:
        """
        Args:
            solver: a FCI solver provided by PySCF.
        """
        super().__init__(None)
        self._solver = solver

    def solve(
        self,
        problem: BaseProblem,
        aux_operators: dict[str, SparseLabelOp | SparsePauliOp] | None = None,
    ) -> ElectronicStructureResult:
        """Finds the ground-state of the provided problem.

        This method is written to match the ``GroundStateSolver`` API of Qiskit Nature but it does
        not support the evaluation of ``aux_operators`` and will ignore them.
        It is also only capable of solving an ``ElectronicStructureProblem`` and will raise an error
        if another problem object is provided.

        Args:
            problem: the problem instance to be solved.
            aux_operators: **ignored**.

        Raises:
            RuntimeError: if a problem other than an ``ElectronicStructureProblem`` is provided.

        Returns:
            The interpreted result object in Qiskit Nature format.
        """
        if aux_operators is not None:
            LOGGER.warning("Ignored.")

        if not isinstance(problem, ElectronicStructureProblem):
            raise RuntimeError("Unsupported problem type.")

        e_ints = problem.hamiltonian.electronic_integrals

        restricted_spin = e_ints.beta_alpha.is_empty()

        one_body: np.ndarray
        two_body: np.ndarray

        if restricted_spin:
            one_body = np.asarray(problem.hamiltonian.electronic_integrals.alpha["+-"])
            two_body = np.asarray(
                to_chemist_ordering(
                    problem.hamiltonian.electronic_integrals.alpha["++--"],
                    index_order=IndexType.PHYSICIST,
                )
            )
        else:
            one_body = np.asarray(
                [
                    problem.hamiltonian.electronic_integrals.alpha["+-"],
                    problem.hamiltonian.electronic_integrals.beta["+-"],
                ]
            )
            two_body = np.asarray(
                [
                    to_chemist_ordering(
                        problem.hamiltonian.electronic_integrals.alpha["++--"],
                        index_order=IndexType.PHYSICIST,
                    ),
                    to_chemist_ordering(
                        problem.hamiltonian.electronic_integrals.alpha_beta["++--"],
                        index_order=IndexType.PHYSICIST,
                    ),
                    to_chemist_ordering(
                        problem.hamiltonian.electronic_integrals.beta["++--"],
                        index_order=IndexType.PHYSICIST,
                    ),
                ]
            )

        energy, ci_vec = self.solver.kernel(
            h1e=one_body,
            eri=two_body,
            norb=problem.num_spatial_orbitals,
            nelec=problem.num_particles,
        )

        _, multiplicity = fci.spin_square(
            ci_vec, norb=problem.num_spatial_orbitals, nelec=problem.num_particles
        )

        density: ElectronicDensity
        if restricted_spin:
            raw_density = self.solver.make_rdm1(
                ci_vec, norb=problem.num_spatial_orbitals, nelec=problem.num_particles
            )
            density = ElectronicDensity.from_raw_integrals(raw_density)
        else:
            raw_density = self.solver.make_rdm1s(
                ci_vec, norb=problem.num_spatial_orbitals, nelec=problem.num_particles
            )
            density = ElectronicDensity.from_raw_integrals(raw_density[0], h1_b=raw_density[1])

        result = ElectronicStructureResult()
        result.computed_energies = np.asarray([energy])
        result.hartree_fock_energy = problem.reference_energy
        result.extracted_transformer_energies = dict(problem.hamiltonian.constants.items())
        result.nuclear_repulsion_energy = result.extracted_transformer_energies.pop(
            "nuclear_repulsion_energy", None
        )
        result.num_particles = [sum(problem.num_particles)]
        result.magnetization = [0.5 * (problem.num_alpha - problem.num_beta)]
        result.total_angular_momentum = [0.25 * (multiplicity**2 - 1)]
        result.electronic_density = density

        return result

    get_qubit_operators = None
    """This class does not deal with qubit operators."""

    def supports_aux_operators(self) -> bool:
        """Returns whether the eigensolver supports auxiliary operators."""
        return False

    @property
    def solver(self):
        """Returns the solver."""
        return self._solver

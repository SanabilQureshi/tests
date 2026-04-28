# flowmol - EPYC + Multi-GPU Port

This branch ports [edwardsmith999/flowmol](https://github.com/edwardsmith999/flowmol)
to make full use of an AMD EPYC 7542 (64C / 128T) workstation with 8x
RTX 3090 GPUs and ~250 GiB of RAM. The original physics is unchanged;
the changes add a second axis of parallelism (OpenMP per MPI rank), a
GPU offload path, and a hardware-aware launcher.

## What changed

| Layer | Original | This port |
|-------|----------|-----------|
| Compiler tuning | generic `-O3 -funroll-loops` | `-O3 -funroll-loops -ftree-vectorize -march=znver2 -mtune=znver2` (`-mavx2`/`-mfma` enabled) |
| MPI bindings | breaks on gfortran-10+ | `messenger.MPI.f90` patched: 1-D status arrays, array section in `MPI_Cart_coords`. Built with `-fallow-argument-mismatch` |
| Per-rank parallelism | none | OpenMP fast paths in: <br> - `simulation_compute_forces_LJ_AP` <br> - `simulation_compute_forces_LJ_neigbr` (full int) <br> - `simulation_compute_forces_LJ_neigbr_halfint` (half int, the default) <br> - `simulation_move_particles_lfv` (all ensembles) <br> - `simulation_move_particles_vv` (all ensembles, both passes) <br> - `simulation_checkrebuild` displacement / vmax check |
| GPU | unused (dead `cuda.inc` from a 2010-era `sm_11` build) | OpenACC kernel for `LJ_AP` on Ampere; per-rank GPU pinning via `CUDA_VISIBLE_DEVICES` |
| Run scripts | none | `runs/launch_epyc.sh` - hybrid MPI + OpenMP, NUMA-aware, optional GPU |

The per-thread shadow-array machinery for half-interaction force
accumulation lives in module `force_omp` at the top of
`src/simulation_compute_forces.f90`. Buffers are allocated once and
grown as needed; only the rows actually touched by the current step
are zeroed.

## Build

The Fortran source still requires only mpif90 (OpenMPI or MPICH).

```bash
cd src
make PLATFORM=epyc-gnu p          # CPU build (gfortran 10+, OpenMPI)
make PLATFORM=epyc-gnu USE_OPENMP=0 p   # opt out of OpenMP
make PLATFORM=nvhpc-gpu p         # CPU + GPU build (NVHPC SDK)
make PLATFORM=gfortran p          # legacy single-thread build
```

`epyc-gnu.inc` defaults to `-march=znver2`; on Zen 3 / Zen 4 either
override (`make PLATFORM=epyc-gnu ARCH=znver3 p`) or accept the small
forward-compat penalty.

## Run

```bash
# 8 ranks (one per GPU), 8 OpenMP threads each, GPU off (CPU-only)
runs/launch_epyc.sh -i src/default.in -r 8 -t 8

# 8 ranks, 8 threads, GPU on (FORCE_LIST=0 / all-pairs in MD.in)
runs/launch_epyc.sh -i MD.in -r 8 -t 8 -g

# 1 rank, 64 threads (single-NUMA test)
runs/launch_epyc.sh -i MD.in -r 1 -t 64
```

The launcher always uses `--bind-to numa` so per-rank OpenMP threads
get one NUMA node (8 cores on the 7542) instead of being trapped on a
single core - the default OpenMPI binding silently serialises hybrid
MPI+OpenMP builds (cost: ~1x parallel scaling instead of Nx). If you
launch by hand, add `--bind-to numa` or `--bind-to none`.

## Tunables

Set in the environment before launch:

| Variable | Default | Purpose |
|----------|---------|---------|
| `OMP_NUM_THREADS` | (unset) | per-rank OpenMP threads |
| `OMP_PROC_BIND` | `close` | thread placement within a NUMA node |
| `OMP_PLACES` | `cores` | place granularity |
| `OMP_WAIT_POLICY` | `active` | spin-wait when cores are dedicated |
| `FLOWMOL_OMP_MIN_NP_NTH` | `16384` | force kernel falls back to serial below this `np * threads` product (avoids overhead on tiny problems) |
| `FLOWMOL_GPU_BACKEND` | (unset) | set to `openacc` to enable GPU offload of `LJ_AP` |
| `CUDA_VISIBLE_DEVICES` | (per-rank) | one GPU per rank; the launch script sets this from `OMPI_COMM_WORLD_LOCAL_RANK` |

## Recommended layouts

| Run shape | MPI ranks | OMP threads | GPU? | When |
|-----------|-----------|-------------|------|------|
| Many small simulations in a sweep | 8 | 8 | no | 1 simulation per NUMA node, no shared state |
| One large simulation, CPU only | 8 | 8 | no | total 64 cores, 8x MPI domain decomposition |
| One large simulation, all GPUs | 8 | 8 | yes (`FORCE_LIST 0`) | dense all-pairs benchmarks; one GPU per rank |
| Quick interactive | 1 | 64 | no | small N, single NUMA node |

## Correctness guards

- The OpenMP fast paths run only when `pressure_outflag`,
  `vflux_outflag`, and `eflux_outflag` are all zero. When any analysis
  flag is set the dispatcher falls through to the original serial
  kernels (those routines mutate global state that races under
  threading).
- The OpenMP fast paths also skip themselves when `np * threads <
  FLOWMOL_OMP_MIN_NP_NTH`, so small problems never pay OMP overhead.
- Smoke-tested against the bundled `default.in`: total energy and
  per-step output match the original gfortran build to ~10 significant
  digits (the residual differs because of FP non-associativity in the
  reduction order - it is not a bug).

## Files added or changed

```
platforms/epyc-gnu.inc                    # gfortran + OpenMP, znver2 tuned
platforms/nvhpc-gpu.inc                   # NVHPC + OpenACC, cc86
runs/launch_epyc.sh                       # hybrid MPI+OMP launcher
src/messenger.MPI.f90                     # MPI binding fixes (1-D status, array section)
src/simulation_compute_forces.f90         # force_omp module + 3 OMP fast paths + 1 OpenACC path
src/simulation_move_particles_lfv.f90     # OMP integrator
src/simulation_move_particles_vv.f90      # OMP integrator
src/simulation_checkrebuild.f90           # OMP displacement check
README_OPTIMIZED.md                       # this file
```

## Known limitations

- The cell-list kernel (`FORCE_LIST 1`) and Soddemann/FENE polymer kernels are not OpenMP-parallelised in this port. The dispatcher routes them to the original serial code; that path still benefits from EPYC vector tuning but does not scale with threads. Adding shadow-array versions follows the same pattern as `simulation_compute_forces_LJ_neigbr_halfint_omp`.
- The OpenACC kernel covers all-pairs (`FORCE_LIST 0`) only. Neighbour-list GPU kernels need the linked-list neighbour structure converted to a flat CSR layout - that is a deeper refactor that I did not attempt here.
- `messenger.MPI.f90` retains the original MPI 2-style explicit interface usage; on toolchains older than gfortran-10 / nvhpc-23 you can drop `-fallow-argument-mismatch` from the platform file.

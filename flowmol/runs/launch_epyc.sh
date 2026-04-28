#!/usr/bin/env bash
#============================================================================
# launch_epyc.sh
#
# Run flowmol on the AMD EPYC 7542 (64C / 128T) + 8x RTX 3090 box.
#
# Usage:
#   ./launch_epyc.sh [-i input.in] [-r ranks] [-t threads_per_rank] [-g]
#
#   -i FILE        input file (default: default.in)
#   -r N           total MPI ranks (default: 8 - one per GPU / NUMA domain)
#   -t N           OpenMP threads per rank (default: cores_per_rank)
#   -g             enable GPU offload (FLOWMOL_GPU_BACKEND=openacc)
#   -d X Y Z       MPI process grid (X*Y*Z must equal -r value)
#
# Hardware-aware defaults:
#   - 8 ranks, 1 per GPU + 8 cores per rank for OpenMP fall-through.
#   - --bind-to numa so each rank gets its own L3 / memory channel and
#     OpenMP threads spread across the 8 cores in that NUMA node, NOT
#     time-shared on a single core (the OpenMPI default --bind-to core
#     is a footgun for hybrid MPI+OpenMP).
#   - CUDA_VISIBLE_DEVICES is set per local rank so GPU bound to NUMA
#     locality.
#============================================================================

set -euo pipefail

INPUT="default.in"
RANKS=8
THREADS=""
GPU=0
PROC_GRID=""

while getopts "i:r:t:gd:h" opt; do
    case "${opt}" in
        i) INPUT="${OPTARG}" ;;
        r) RANKS="${OPTARG}" ;;
        t) THREADS="${OPTARG}" ;;
        g) GPU=1 ;;
        d) PROC_GRID="${OPTARG}" ;;
        h)
            sed -n '/^# Usage:/,/^#=====/p' "$0"
            exit 0
            ;;
        *) exit 1 ;;
    esac
done

# Total physical cores - default EPYC 7542 layout, override via env.
TOTAL_CORES="${FLOWMOL_TOTAL_CORES:-64}"
CORES_PER_RANK=$(( TOTAL_CORES / RANKS ))
[[ -z "${THREADS}" ]] && THREADS="${CORES_PER_RANK}"

# Fall-back if requested ranks > cores
if (( CORES_PER_RANK < 1 )); then
    CORES_PER_RANK=1
    THREADS=1
fi

EXE="$(dirname "$(readlink -f "$0")")/../src/parallel_md.exe"
if [[ ! -x "${EXE}" ]]; then
    echo "Build the binary first:" >&2
    echo "    (cd ../src && make PLATFORM=epyc-gnu p)" >&2
    exit 1
fi

export OMP_NUM_THREADS="${THREADS}"
export OMP_PROC_BIND=close       # threads inside a rank stay packed in NUMA
export OMP_PLACES=cores
export OMP_WAIT_POLICY=active    # busy-wait is the right policy with dedicated cores

if (( GPU == 1 )); then
    export FLOWMOL_GPU_BACKEND=openacc
    # Each rank sees one GPU. CUDA_VISIBLE_DEVICES is set per-rank inside
    # the wrapper script below.
fi

echo "============================================================"
echo " flowmol launch: ${RANKS} ranks x ${THREADS} OpenMP threads"
echo " input file:    ${INPUT}"
echo " GPU offload:   $([[ ${GPU} -eq 1 ]] && echo on || echo off)"
echo " bind-to:       numa  (per-rank NUMA isolation)"
echo "============================================================"

# Per-rank GPU pinning wrapper. OpenMPI exposes the local rank via
# OMPI_COMM_WORLD_LOCAL_RANK; MPICH uses MPI_LOCALRANKID. We try both.
WRAPPER=$(mktemp)
trap 'rm -f "${WRAPPER}"' EXIT
cat >"${WRAPPER}" <<'EOWRAP'
#!/usr/bin/env bash
LOCAL_RANK="${OMPI_COMM_WORLD_LOCAL_RANK:-${MPI_LOCALRANKID:-${PMI_LOCAL_RANK:-0}}}"
export CUDA_VISIBLE_DEVICES="${LOCAL_RANK}"
exec "$@"
EOWRAP
chmod +x "${WRAPPER}"

BIND="${FLOWMOL_BIND:-numa}"
MPIEXEC_ARGS=(--bind-to "${BIND}")
if [[ "${BIND}" == "numa" ]]; then
    MPIEXEC_ARGS+=(--map-by "ppr:1:numa")
fi
if ! mpiexec --help 2>&1 | grep -q -- '--bind-to'; then
    # MPICH style
    MPIEXEC_ARGS=(-bind-to "${BIND}")
fi

mpiexec --allow-run-as-root "${MPIEXEC_ARGS[@]}" -n "${RANKS}" \
    "${WRAPPER}" "${EXE}" -i "${INPUT}"

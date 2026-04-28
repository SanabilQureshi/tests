#============================================================================
# Platform: epyc-gnu
#   GNU Fortran + OpenMPI tuned for an AMD EPYC + multi-GPU workstation.
#
#   - Targets Zen2 (znver2) but works on Zen3/Zen4 too.
#   - Enables OpenMP so a single MPI rank can fan out across many cores.
#   - Adds the gfortran-10+ flags needed to build flowmol's MPI bindings
#     on modern toolchains.
#   - A compatible mpif90 is assumed in PATH (MPICH or OpenMPI).
#
# Recommended layout on the 8-GPU / 64-core EPYC box:
#     mpiexec -n <NPX*NPY*NPZ> --bind-to numa  ./parallel_md.exe -i MD.in
#     OMP_NUM_THREADS=<cores_per_rank> OMP_PLACES=cores OMP_PROC_BIND=close
#
# See ../runs/launch_epyc.sh for a ready-to-run multi-rank/threaded launcher.
#============================================================================

BUILD_LIST := debug opt

ifndef USE_COUPLER
  $(warning USE_COUPLER not defined for code $(NAME), assuming uncoupled)
  FPP_FLAGS     := -cpp -DUSE_COUPLER=0
else
  FPP_FLAGS_no  := -cpp -DUSE_COUPLER=0
  FPP_FLAGS_yes := -cpp -DUSE_COUPLER=1
  FPP_FLAGS     := $(FPP_FLAGS_$(USE_COUPLER))
endif

ifndef BUILD
  BUILD := opt
else
  $(if $(findstring $(BUILD), $(BUILD_LIST)),,$(error unknown value of BUILD = $(BUILD)))
endif

ifdef NO_ASSMBLY_HEAVISIDES
  FPP_FLAGS := $(FPP_FLAGS) -DASSMBLY_HEAVISIDES=0
endif

ifdef USE_LAPACK
  FPP_FLAGS := $(FPP_FLAGS) -DUSE_LAPACK=1 -fPIC
  LDFLAGS   := $(LDFLAGS) -llapack -lblas
endif

# Allow an opt-in OpenACC GPU build, see platforms/nvhpc-gpu.inc for the
# supported NVHPC path. Setting USE_OPENACC=1 here only flips a CPP guard
# so that GPU sources are excluded by default with gfortran.
ifdef USE_OPENACC
  FPP_FLAGS := $(FPP_FLAGS) -DUSE_OPENACC=1
endif

# By default flowmol's force / integrator hot loops are OpenMP-parallel.
# Set USE_OPENMP=0 to fall back to a serial build of those kernels.
USE_OPENMP ?= 1
ifeq ($(strip $(USE_OPENMP)),1)
  OMP_FLAGS := -fopenmp
  FPP_FLAGS := $(FPP_FLAGS) -DUSE_OPENMP=1
else
  OMP_FLAGS :=
endif

F90 := mpif90
LD  := $(F90)

ifdef OBJ_DIR
  FLAG_OBJ_DIR := -J$(OBJ_DIR)
else
  FLAG_OBJ_DIR :=
endif

UNDERSCORING := -fno-underscoring

# --- gfortran-10+ MPI-binding compatibility ---
# The original messenger.MPI.f90 has a few mismatches against the strict
# explicit interfaces in the F90 'mpi' module shipped by current MPICH /
# OpenMPI. The argument-mismatch flag demotes them to warnings; the rest
# of the code is type-clean.
COMPAT_FLAGS := -fallow-argument-mismatch

# Architecture / vectorisation tuning. znver2 lights up AVX2 + FMA on
# the EPYC 7542 and is forward-compatible with Zen3 (Milan) and Zen4
# (Genoa). Override on the command line with ARCH=znver3 etc. if you
# move to a newer chip.
ARCH ?= znver2
ARCH_FLAGS := -march=$(ARCH) -mtune=$(ARCH)

# Aggressive but safe optimisation flags. -ffast-math is opt-in via
# FAST_MATH=1 because it relaxes IEEE rounding (energy conservation is
# unaffected at the timesteps used here, but be aware of it).
FAST_MATH ?= 0
ifeq ($(strip $(FAST_MATH)),1)
  MATH_FLAGS := -ffast-math -fno-finite-math-only
else
  MATH_FLAGS :=
endif

OPT_FLAGS := -O3 -funroll-loops -ftree-vectorize -fno-stack-arrays \
             -fopt-info-vec-optimized=$(OBJ_DIR)/vectorization.log \
             $(ARCH_FLAGS) $(MATH_FLAGS)

FLAGS_BASE_debug := $(FPP_FLAGS) -fbacktrace -g -ggdb -O0 -W -Wsurprising \
                    -Wampersand -Waliasing -fbounds-check \
                    -ffpe-trap=zero,overflow,underflow $(FLAG_OBJ_DIR) \
                    $(UNDERSCORING) $(OMP_FLAGS) $(COMPAT_FLAGS)
FLAGS_BASE_opt   := $(FPP_FLAGS) $(OPT_FLAGS) $(FLAG_OBJ_DIR) \
                    $(UNDERSCORING) $(OMP_FLAGS) $(COMPAT_FLAGS)

ifeq ($(strip $(NAME)),couette)
  FFLAGS := $(FLAGS_BASE_$(BUILD)) -r8
else
  FFLAGS := $(FLAGS_BASE_$(BUILD))
endif

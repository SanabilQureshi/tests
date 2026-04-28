#============================================================================
# Platform: nvhpc-gpu
#   NVIDIA HPC SDK (formerly PGI) for the EPYC + 8x RTX 3090 box.
#
#   - mpif90 wrapper from a CUDA-aware MPI built against NVHPC's nvfortran
#     (the SDK ships hpcx-mpi which works out of the box).
#   - Targets the GA10x architecture (cc86 = Ampere consumer).
#   - Enables OpenMP (multicore) AND OpenACC (gpu) so the dispatcher
#     can pick GPU offload via USE_OPENACC=1 at compile time.
#
# A typical multi-GPU launch on the 8x RTX 3090 system:
#   export FLOWMOL_GPU_BACKEND=openacc
#   mpiexec --bind-to numa -n 8 \
#     bash -c 'export CUDA_VISIBLE_DEVICES=$OMPI_COMM_WORLD_LOCAL_RANK; \
#              ./parallel_md.exe -i MD.in'
#
#   With PROCESSORS 2 2 2 in MD.in (8 ranks total), each rank gets its
#   own GPU and 8 EPYC cores worth of OpenMP fall-through for non-GPU
#   kernels. See ../runs/launch_epyc.sh.
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

USE_OPENMP   ?= 1
USE_OPENACC  ?= 1
GPU_ARCH     ?= cc86
ARCH         ?= zen2

ifeq ($(strip $(USE_OPENMP)),1)
  OMP_FLAGS := -mp
  FPP_FLAGS := $(FPP_FLAGS) -DUSE_OPENMP=1
else
  OMP_FLAGS :=
endif

ifeq ($(strip $(USE_OPENACC)),1)
  ACC_FLAGS := -acc=gpu -gpu=$(GPU_ARCH),fastmath -Minfo=accel
  FPP_FLAGS := $(FPP_FLAGS) -DUSE_OPENACC=1
else
  ACC_FLAGS :=
endif

F90 := mpif90
LD  := $(F90)

ifdef OBJ_DIR
  FLAG_OBJ_DIR := -module $(OBJ_DIR)
else
  FLAG_OBJ_DIR :=
endif

# nvfortran's argument-mismatch demotion is the same idea as gfortran's
# -fallow-argument-mismatch: keep messenger.MPI.f90 building.
COMPAT_FLAGS := -Mallow_size_of_assumed_size_array

OPT_FLAGS := -O3 -fast -tp=$(ARCH) -Munroll -Mvect=simd

FLAGS_BASE_debug := $(FPP_FLAGS) -O0 -g -traceback -Mbounds -Mchkfpstk \
                    $(FLAG_OBJ_DIR) $(OMP_FLAGS) $(ACC_FLAGS) $(COMPAT_FLAGS)
FLAGS_BASE_opt   := $(FPP_FLAGS) $(OPT_FLAGS) $(FLAG_OBJ_DIR) \
                    $(OMP_FLAGS) $(ACC_FLAGS) $(COMPAT_FLAGS)

ifeq ($(strip $(NAME)),couette)
  FFLAGS := $(FLAGS_BASE_$(BUILD)) -r8
else
  FFLAGS := $(FLAGS_BASE_$(BUILD))
endif

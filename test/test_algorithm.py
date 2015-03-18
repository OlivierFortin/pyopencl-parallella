#! /usr/bin/env python

from __future__ import division, with_statement

__copyright__ = "Copyright (C) 2013 Andreas Kloeckner"

__license__ = """
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

import numpy as np
import numpy.linalg as la
import sys
from pytools import memoize
from test_array import general_clrand

import pytest

import pyopencl as cl
import pyopencl.array as cl_array  # noqa
from pyopencl.tools import (  # noqa
        pytest_generate_tests_for_pyopencl as pytest_generate_tests)
from pyopencl.characterize import has_double_support
from pyopencl.scan import InclusiveScanKernel, ExclusiveScanKernel


# {{{ elementwise

def test_elwise_kernel(ctx_factory):
    context = ctx_factory()
    queue = cl.CommandQueue(context)

    from pyopencl.clrandom import rand as clrand

    a_gpu = clrand(queue, (50,), np.float32)
    b_gpu = clrand(queue, (50,), np.float32)

    from pyopencl.elementwise import ElementwiseKernel
    lin_comb = ElementwiseKernel(context,
            "float a, float *x, float b, float *y, float *z",
            "z[i] = a*x[i] + b*y[i]",
            "linear_combination")

    c_gpu = cl_array.empty_like(a_gpu)
    lin_comb(5, a_gpu, 6, b_gpu, c_gpu)

    assert la.norm((c_gpu - (5 * a_gpu + 6 * b_gpu)).get()) < 1e-5


def test_elwise_kernel_with_options(ctx_factory):
    from pyopencl.clrandom import rand as clrand
    from pyopencl.elementwise import ElementwiseKernel

    context = ctx_factory()
    queue = cl.CommandQueue(context)

    in_gpu = clrand(queue, (50,), np.float32)

    options = ['-D', 'ADD_ONE']
    add_one = ElementwiseKernel(
        context,
        "float* out, const float *in",
        """
        out[i] = in[i]
        #ifdef ADD_ONE
            +1
        #endif
        ;
        """,
        options=options,
        )

    out_gpu = cl_array.empty_like(in_gpu)
    add_one(out_gpu, in_gpu)

    gt = in_gpu.get() + 1
    gv = out_gpu.get()
    assert la.norm(gv - gt) < 1e-5


def test_ranged_elwise_kernel(ctx_factory):
    context = ctx_factory()
    queue = cl.CommandQueue(context)

    from pyopencl.elementwise import ElementwiseKernel
    set_to_seven = ElementwiseKernel(context,
            "float *z", "z[i] = 7", "set_to_seven")

    for i, slc in enumerate([
            slice(5, 20000),
            slice(5, 20000, 17),
            slice(3000, 5, -1),
            slice(1000, -1),
            ]):

        a_gpu = cl_array.zeros(queue, (50000,), dtype=np.float32)
        a_cpu = np.zeros(a_gpu.shape, a_gpu.dtype)

        a_cpu[slc] = 7
        set_to_seven(a_gpu, slice=slc)

        assert (a_cpu == a_gpu.get()).all()


def test_take(ctx_factory):
    context = ctx_factory()
    queue = cl.CommandQueue(context)

    idx = cl_array.arange(queue, 0, 200000, 2, dtype=np.uint32)
    a = cl_array.arange(queue, 0, 600000, 3, dtype=np.float32)
    result = cl_array.take(a, idx)
    assert ((3 * idx).get() == result.get()).all()


def test_arange(ctx_factory):
    context = ctx_factory()
    queue = cl.CommandQueue(context)

    n = 5000
    a = cl_array.arange(queue, n, dtype=np.float32)
    assert (np.arange(n, dtype=np.float32) == a.get()).all()


def test_reverse(ctx_factory):
    context = ctx_factory()
    queue = cl.CommandQueue(context)

    n = 5000
    a = np.arange(n).astype(np.float32)
    a_gpu = cl_array.to_device(queue, a)

    a_gpu = a_gpu.reverse()

    assert (a[::-1] == a_gpu.get()).all()


def test_if_positive(ctx_factory):
    context = ctx_factory()
    queue = cl.CommandQueue(context)

    from pyopencl.clrandom import rand as clrand

    l = 20000
    a_gpu = clrand(queue, (l,), np.float32)
    b_gpu = clrand(queue, (l,), np.float32)
    a = a_gpu.get()
    b = b_gpu.get()

    max_a_b_gpu = cl_array.maximum(a_gpu, b_gpu)
    min_a_b_gpu = cl_array.minimum(a_gpu, b_gpu)

    print(max_a_b_gpu)
    print(np.maximum(a, b))

    assert la.norm(max_a_b_gpu.get() - np.maximum(a, b)) == 0
    assert la.norm(min_a_b_gpu.get() - np.minimum(a, b)) == 0


def test_take_put(ctx_factory):
    context = ctx_factory()
    queue = cl.CommandQueue(context)

    for n in [5, 17, 333]:
        one_field_size = 8
        buf_gpu = cl_array.zeros(queue,
                n * one_field_size, dtype=np.float32)
        dest_indices = cl_array.to_device(queue,
                np.array([0, 1, 2,  3, 32, 33, 34, 35], dtype=np.uint32))
        read_map = cl_array.to_device(queue,
                np.array([7, 6, 5, 4, 3, 2, 1, 0], dtype=np.uint32))

        cl_array.multi_take_put(
                arrays=[buf_gpu for i in range(n)],
                dest_indices=dest_indices,
                src_indices=read_map,
                src_offsets=[i * one_field_size for i in range(n)],
                dest_shape=(96,))


def test_astype(ctx_factory):
    context = ctx_factory()
    queue = cl.CommandQueue(context)

    from pyopencl.clrandom import rand as clrand

    if not has_double_support(context.devices[0]):
        from pytest import skip
        skip("double precision not supported on %s" % context.devices[0])

    a_gpu = clrand(queue, (2000,), dtype=np.float32)

    a = a_gpu.get().astype(np.float64)
    a2 = a_gpu.astype(np.float64).get()

    assert a2.dtype == np.float64
    assert la.norm(a - a2) == 0, (a, a2)

    a_gpu = clrand(queue, (2000,), dtype=np.float64)

    a = a_gpu.get().astype(np.float32)
    a2 = a_gpu.astype(np.float32).get()

    assert a2.dtype == np.float32
    assert la.norm(a - a2) / la.norm(a) < 1e-7

# }}}


# {{{ reduction

def test_sum(ctx_factory):
    from pytest import importorskip
    importorskip("mako")

    context = ctx_factory()
    queue = cl.CommandQueue(context)

    n = 200000
    for dtype in [np.float32, np.complex64]:
        a_gpu = general_clrand(queue, (n,), dtype)

        a = a_gpu.get()

        for slc in [
                slice(None),
                slice(1000, 3000),
                slice(1000, -3000),
                slice(1000, None),
                ]:
            sum_a = np.sum(a[slc])
            sum_a_gpu = cl_array.sum(a_gpu[slc]).get()

            assert abs(sum_a_gpu - sum_a) / abs(sum_a) < 1e-4


def test_minmax(ctx_factory):
    from pytest import importorskip
    importorskip("mako")

    context = ctx_factory()
    queue = cl.CommandQueue(context)

    from pyopencl.clrandom import rand as clrand

    if has_double_support(context.devices[0]):
        dtypes = [np.float64, np.float32, np.int32]
    else:
        dtypes = [np.float32, np.int32]

    for what in ["min", "max"]:
        for dtype in dtypes:
            a_gpu = clrand(queue, (200000,), dtype)
            a = a_gpu.get()

            op_a = getattr(np, what)(a)
            op_a_gpu = getattr(cl_array, what)(a_gpu).get()

            assert op_a_gpu == op_a, (op_a_gpu, op_a, dtype, what)


def test_subset_minmax(ctx_factory):
    from pytest import importorskip
    importorskip("mako")

    context = ctx_factory()
    queue = cl.CommandQueue(context)

    from pyopencl.clrandom import rand as clrand

    l_a = 200000
    gran = 5
    l_m = l_a - l_a // gran + 1

    if has_double_support(context.devices[0]):
        dtypes = [np.float64, np.float32, np.int32]
    else:
        dtypes = [np.float32, np.int32]

    for dtype in dtypes:
        a_gpu = clrand(queue, (l_a,), dtype)
        a = a_gpu.get()

        meaningful_indices_gpu = cl_array.zeros(
                queue, l_m, dtype=np.int32)
        meaningful_indices = meaningful_indices_gpu.get()
        j = 0
        for i in range(len(meaningful_indices)):
            meaningful_indices[i] = j
            j = j + 1
            if j % gran == 0:
                j = j + 1

        meaningful_indices_gpu = cl_array.to_device(
                queue, meaningful_indices)
        b = a[meaningful_indices]

        min_a = np.min(b)
        min_a_gpu = cl_array.subset_min(meaningful_indices_gpu, a_gpu).get()

        assert min_a_gpu == min_a


def test_dot(ctx_factory):
    from pytest import importorskip
    importorskip("mako")

    context = ctx_factory()
    queue = cl.CommandQueue(context)

    dtypes = [np.float32, np.complex64]
    if has_double_support(context.devices[0]):
        dtypes.extend([np.float64, np.complex128])

    for a_dtype in dtypes:
        for b_dtype in dtypes:
            print(a_dtype, b_dtype)
            a_gpu = general_clrand(queue, (200000,), a_dtype)
            a = a_gpu.get()
            b_gpu = general_clrand(queue, (200000,), b_dtype)
            b = b_gpu.get()

            dot_ab = np.dot(a, b)
            dot_ab_gpu = cl_array.dot(a_gpu, b_gpu).get()

            assert abs(dot_ab_gpu - dot_ab) / abs(dot_ab) < 1e-4

            vdot_ab = np.vdot(a, b)
            vdot_ab_gpu = cl_array.vdot(a_gpu, b_gpu).get()

            assert abs(vdot_ab_gpu - vdot_ab) / abs(vdot_ab) < 1e-4


@memoize
def make_mmc_dtype(device):
    dtype = np.dtype([
        ("cur_min", np.int32),
        ("cur_max", np.int32),
        ("pad", np.int32),
        ])

    name = "minmax_collector"
    from pyopencl.tools import get_or_register_dtype, match_dtype_to_c_struct

    dtype, c_decl = match_dtype_to_c_struct(device, name, dtype)
    dtype = get_or_register_dtype(name, dtype)

    return dtype, c_decl


def test_struct_reduce(ctx_factory):
    pytest.importorskip("mako")

    context = ctx_factory()
    queue = cl.CommandQueue(context)

    dev, = context.devices
    if (dev.vendor == "NVIDIA" and dev.platform.vendor == "Apple"
            and dev.driver_version == "8.12.47 310.40.00.05f01"):
        pytest.skip("causes a compiler hang on Apple/Nv GPU")

    mmc_dtype, mmc_c_decl = make_mmc_dtype(context.devices[0])

    preamble = mmc_c_decl + r"""//CL//

    minmax_collector mmc_neutral()
    {
        // FIXME: needs infinity literal in real use, ok here
        minmax_collector result;
        result.cur_min = 1<<30;
        result.cur_max = -(1<<30);
        return result;
    }

    minmax_collector mmc_from_scalar(float x)
    {
        minmax_collector result;
        result.cur_min = x;
        result.cur_max = x;
        return result;
    }

    minmax_collector agg_mmc(minmax_collector a, minmax_collector b)
    {
        minmax_collector result = a;
        if (b.cur_min < result.cur_min)
            result.cur_min = b.cur_min;
        if (b.cur_max > result.cur_max)
            result.cur_max = b.cur_max;
        return result;
    }

    """

    from pyopencl.clrandom import rand as clrand
    a_gpu = clrand(queue, (20000,), dtype=np.int32, a=0, b=10**6)
    a = a_gpu.get()

    from pyopencl.reduction import ReductionKernel
    red = ReductionKernel(context, mmc_dtype,
            neutral="mmc_neutral()",
            reduce_expr="agg_mmc(a, b)", map_expr="mmc_from_scalar(x[i])",
            arguments="__global int *x", preamble=preamble)

    minmax = red(a_gpu).get()
    #print minmax["cur_min"], minmax["cur_max"]
    #print np.min(a), np.max(a)

    assert abs(minmax["cur_min"] - np.min(a)) < 1e-5
    assert abs(minmax["cur_max"] - np.max(a)) < 1e-5

# }}}


# {{{ scan-related

def summarize_error(obtained, desired, orig, thresh=1e-5):
    from pytest import importorskip
    importorskip("mako")

    err = obtained - desired
    ok_count = 0
    bad_count = 0

    bad_limit = 200

    def summarize_counts():
        if ok_count:
            entries.append("<%d ok>" % ok_count)
        if bad_count >= bad_limit:
            entries.append("<%d more bad>" % (bad_count-bad_limit))

    entries = []
    for i, val in enumerate(err):
        if abs(val) > thresh:
            if ok_count:
                summarize_counts()
                ok_count = 0

            bad_count += 1

            if bad_count < bad_limit:
                entries.append("%r (want: %r, got: %r, orig: %r)" % (
                    obtained[i], desired[i], obtained[i], orig[i]))
        else:
            if bad_count:
                summarize_counts()
                bad_count = 0

            ok_count += 1

    summarize_counts()

    return " ".join(entries)

scan_test_counts = [
    10,
    2 ** 8 - 1,
    2 ** 8,
    2 ** 8 + 1,
    2 ** 10 - 5,
    2 ** 10,
    2 ** 10 + 5,
    2 ** 12 - 5,
    2 ** 12,
    2 ** 12 + 5,
    2 ** 20 - 2 ** 18,
    2 ** 20 - 2 ** 18 + 5,
    2 ** 20 + 1,
    2 ** 20,
    2 ** 23 + 3,
    # larger sizes cause out of memory on low-end AMD APUs
    ]


@pytest.mark.parametrize("dtype", [np.int32, np.int64])
@pytest.mark.parametrize("scan_cls", [InclusiveScanKernel, ExclusiveScanKernel])
def test_scan(ctx_factory, dtype, scan_cls):
    from pytest import importorskip
    importorskip("mako")

    context = ctx_factory()
    queue = cl.CommandQueue(context)

    knl = scan_cls(context, dtype, "a+b", "0")

    for n in scan_test_counts:
        host_data = np.random.randint(0, 10, n).astype(dtype)
        dev_data = cl_array.to_device(queue, host_data)

        # /!\ fails on Nv GT2?? for some drivers
        assert (host_data == dev_data.get()).all()

        knl(dev_data)

        desired_result = np.cumsum(host_data, axis=0)
        if scan_cls is ExclusiveScanKernel:
            desired_result -= host_data

        is_ok = (dev_data.get() == desired_result).all()
        if 1 and not is_ok:
            print("something went wrong, summarizing error...")
            print(summarize_error(dev_data.get(), desired_result, host_data))

        print("dtype:%s n:%d %s worked:%s" % (dtype, n, scan_cls, is_ok))
        assert is_ok
        from gc import collect
        collect()


def test_copy_if(ctx_factory):
    from pytest import importorskip
    importorskip("mako")

    context = ctx_factory()
    queue = cl.CommandQueue(context)

    from pyopencl.clrandom import rand as clrand
    for n in scan_test_counts:
        a_dev = clrand(queue, (n,), dtype=np.int32, a=0, b=1000)
        a = a_dev.get()

        from pyopencl.algorithm import copy_if

        crit = a_dev.dtype.type(300)
        selected = a[a > crit]
        selected_dev, count_dev, evt = copy_if(
                a_dev, "ary[i] > myval", [("myval", crit)])

        assert (selected_dev.get()[:count_dev.get()] == selected).all()
        from gc import collect
        collect()


def test_partition(ctx_factory):
    from pytest import importorskip
    importorskip("mako")

    context = ctx_factory()
    queue = cl.CommandQueue(context)

    from pyopencl.clrandom import rand as clrand
    for n in scan_test_counts:
        print("part", n)

        a_dev = clrand(queue, (n,), dtype=np.int32, a=0, b=1000)
        a = a_dev.get()

        crit = a_dev.dtype.type(300)
        true_host = a[a > crit]
        false_host = a[a <= crit]

        from pyopencl.algorithm import partition
        true_dev, false_dev, count_true_dev, evt = partition(
                a_dev, "ary[i] > myval", [("myval", crit)])

        count_true_dev = count_true_dev.get()

        assert (true_dev.get()[:count_true_dev] == true_host).all()
        assert (false_dev.get()[:n-count_true_dev] == false_host).all()


def test_unique(ctx_factory):
    from pytest import importorskip
    importorskip("mako")

    context = ctx_factory()
    queue = cl.CommandQueue(context)

    from pyopencl.clrandom import rand as clrand
    for n in scan_test_counts:
        a_dev = clrand(queue, (n,), dtype=np.int32, a=0, b=1000)
        a = a_dev.get()
        a = np.sort(a)
        a_dev = cl_array.to_device(queue, a)

        a_unique_host = np.unique(a)

        from pyopencl.algorithm import unique
        a_unique_dev, count_unique_dev, evt = unique(a_dev)

        count_unique_dev = count_unique_dev.get()

        assert (a_unique_dev.get()[:count_unique_dev] == a_unique_host).all()
        from gc import collect
        collect()


def test_index_preservation(ctx_factory):
    from pytest import importorskip
    importorskip("mako")

    context = ctx_factory()
    queue = cl.CommandQueue(context)

    from pyopencl.scan import GenericScanKernel, GenericDebugScanKernel
    classes = [GenericScanKernel]

    dev = context.devices[0]
    if dev.type & cl.device_type.CPU:
        classes.append(GenericDebugScanKernel)

    for cls in classes:
        for n in scan_test_counts:
            knl = cls(
                    context, np.int32,
                    arguments="__global int *out",
                    input_expr="i",
                    scan_expr="b", neutral="0",
                    output_statement="""
                        out[i] = item;
                        """)

            out = cl_array.empty(queue, n, dtype=np.int32)
            knl(out)

            assert (out.get() == np.arange(n)).all()
            from gc import collect
            collect()


def test_segmented_scan(ctx_factory):
    from pytest import importorskip
    importorskip("mako")

    context = ctx_factory()
    queue = cl.CommandQueue(context)

    from pyopencl.tools import dtype_to_ctype
    dtype = np.int32
    ctype = dtype_to_ctype(dtype)

    #for is_exclusive in [False, True]:
    for is_exclusive in [True, False]:
        if is_exclusive:
            output_statement = "out[i] = prev_item"
        else:
            output_statement = "out[i] = item"

        from pyopencl.scan import GenericScanKernel
        knl = GenericScanKernel(context, dtype,
                arguments="__global %s *ary, __global char *segflags, "
                    "__global %s *out" % (ctype, ctype),
                input_expr="ary[i]",
                scan_expr="across_seg_boundary ? b : (a+b)", neutral="0",
                is_segment_start_expr="segflags[i]",
                output_statement=output_statement,
                options=[])

        np.set_printoptions(threshold=2000)
        from random import randrange
        from pyopencl.clrandom import rand as clrand
        for n in scan_test_counts:
            a_dev = clrand(queue, (n,), dtype=dtype, a=0, b=10)
            a = a_dev.get()

            if 10 <= n < 20:
                seg_boundaries_values = [
                        [0, 9],
                        [0, 3],
                        [4, 6],
                        ]
            else:
                seg_boundaries_values = []
                for i in range(10):
                    seg_boundary_count = max(2, min(100, randrange(0, int(0.4*n))))
                    seg_boundaries = [
                            randrange(n) for i in range(seg_boundary_count)]
                    if n >= 1029:
                        seg_boundaries.insert(0, 1028)
                    seg_boundaries.sort()
                    seg_boundaries_values.append(seg_boundaries)

            for seg_boundaries in seg_boundaries_values:
                #print "BOUNDARIES", seg_boundaries
                #print a

                seg_boundary_flags = np.zeros(n, dtype=np.uint8)
                seg_boundary_flags[seg_boundaries] = 1
                seg_boundary_flags_dev = cl_array.to_device(
                        queue, seg_boundary_flags)

                seg_boundaries.insert(0, 0)

                result_host = a.copy()
                for i, seg_start in enumerate(seg_boundaries):
                    if i+1 < len(seg_boundaries):
                        seg_end = seg_boundaries[i+1]
                    else:
                        seg_end = None

                    if is_exclusive:
                        result_host[seg_start+1:seg_end] = np.cumsum(
                                a[seg_start:seg_end][:-1])
                        result_host[seg_start] = 0
                    else:
                        result_host[seg_start:seg_end] = np.cumsum(
                                a[seg_start:seg_end])

                #print "REF", result_host

                result_dev = cl_array.empty_like(a_dev)
                knl(a_dev, seg_boundary_flags_dev, result_dev)

                #print "RES", result_dev
                is_correct = (result_dev.get() == result_host).all()
                if not is_correct:
                    diff = result_dev.get() - result_host
                    print("RES-REF", diff)
                    print("ERRWHERE", np.where(diff))
                    print(n, list(seg_boundaries))

                assert is_correct
                from gc import collect
                collect()

            print("%d excl:%s done" % (n, is_exclusive))


def test_sort(ctx_factory):
    from pytest import importorskip
    importorskip("mako")

    context = ctx_factory()
    queue = cl.CommandQueue(context)

    dtype = np.int32

    from pyopencl.algorithm import RadixSort
    sort = RadixSort(context, "int *ary", key_expr="ary[i]",
            sort_arg_names=["ary"])

    from pyopencl.clrandom import RanluxGenerator
    rng = RanluxGenerator(queue, seed=15)

    from time import time

    # intermediate arrays for largest size cause out-of-memory on low-end GPUs
    for n in scan_test_counts[:-1]:
        print(n)

        print("  rng")
        a_dev = rng.uniform(queue, (n,), dtype=dtype, a=0, b=2**16)
        a = a_dev.get()

        dev_start = time()
        print("  device")
        (a_dev_sorted,), evt = sort(a_dev, key_bits=16)
        queue.finish()
        dev_end = time()
        print("  numpy")
        a_sorted = np.sort(a)
        numpy_end = time()

        numpy_elapsed = numpy_end-dev_end
        dev_elapsed = dev_end-dev_start
        print ("  dev: %.2f MKeys/s numpy: %.2f MKeys/s ratio: %.2fx" % (
                1e-6*n/dev_elapsed, 1e-6*n/numpy_elapsed, numpy_elapsed/dev_elapsed))
        assert (a_dev_sorted.get() == a_sorted).all()


def test_list_builder(ctx_factory):
    from pytest import importorskip
    importorskip("mako")

    context = ctx_factory()
    queue = cl.CommandQueue(context)

    from pyopencl.algorithm import ListOfListsBuilder
    builder = ListOfListsBuilder(context, [("mylist", np.int32)], """//CL//
            void generate(LIST_ARG_DECL USER_ARG_DECL index_type i)
            {
                int count = i % 4;
                for (int j = 0; j < count; ++j)
                {
                    APPEND_mylist(count);
                }
            }
            """, arg_decls=[])

    result, evt = builder(queue, 2000)

    inf = result["mylist"]
    assert inf.count == 3000
    assert (inf.lists.get()[-6:] == [1, 2, 2, 3, 3, 3]).all()


def test_key_value_sorter(ctx_factory):
    from pytest import importorskip
    importorskip("mako")

    context = ctx_factory()
    queue = cl.CommandQueue(context)

    n = 10**5
    nkeys = 2000
    from pyopencl.clrandom import rand as clrand
    keys = clrand(queue, n, np.int32, b=nkeys)
    values = clrand(queue, n, np.int32, b=n).astype(np.int64)

    assert np.max(keys.get()) < nkeys

    from pyopencl.algorithm import KeyValueSorter
    kvs = KeyValueSorter(context)
    starts, lists, evt = kvs(queue, keys, values, nkeys, starts_dtype=np.int32)

    starts = starts.get()
    lists = lists.get()

    mydict = dict()
    for k, v in zip(keys.get(), values.get()):
        mydict.setdefault(k, []).append(v)

    for i in range(nkeys):
        start, end = starts[i:i+2]
        assert sorted(mydict[i]) == sorted(lists[start:end])

# }}}


if __name__ == "__main__":
    if len(sys.argv) > 1:
        exec(sys.argv[1])
    else:
        from py.test.cmdline import main
        main([__file__])

# vim: filetype=pyopencl:fdm=marker

#include "char_decomposition_cache.hpp"
#include "index_singlet_project.hpp"

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <sqlite3.h>

#include <limits>
#include <string>
#include <utility>

namespace py = pybind11;
namespace chars = n2scftdb::characters;

namespace {

std::int64_t integer64(py::handle value) {
    if (!PyLong_Check(value.ptr()) || PyBool_Check(value.ptr())) {
        throw py::type_error("expected an integer; floats and booleans are not accepted");
    }
    const long long result = PyLong_AsLongLong(value.ptr());
    if (result == -1 && PyErr_Occurred()) throw py::error_already_set();
    return result;
}

int small_integer(py::handle value) {
    const auto result = integer64(value);
    if (result < std::numeric_limits<int>::min() || result > std::numeric_limits<int>::max()) {
        PyErr_SetString(PyExc_OverflowError, "integer is outside the native rank range");
        throw py::error_already_set();
    }
    return static_cast<int>(result);
}

template <class Function>
auto released(Function&& function) {
    py::gil_scoped_release release;
    return function();
}

void translate_sqlite_error(std::exception_ptr exception) {
    try {
        if (exception) std::rethrow_exception(exception);
    } catch (const chars::SqliteError& error) {
        const char* category = "DatabaseError";
        switch (error.code() & 0xff) {
            case SQLITE_ERROR: case SQLITE_PERM: case SQLITE_ABORT:
            case SQLITE_BUSY: case SQLITE_LOCKED: case SQLITE_READONLY:
            case SQLITE_INTERRUPT: case SQLITE_IOERR: case SQLITE_FULL:
            case SQLITE_CANTOPEN: case SQLITE_PROTOCOL: case SQLITE_EMPTY:
            case SQLITE_SCHEMA:
                category = "OperationalError"; break;
            case SQLITE_CONSTRAINT: case SQLITE_MISMATCH:
                category = "IntegrityError"; break;
            case SQLITE_TOOBIG:
                category = "DataError"; break;
            case SQLITE_MISUSE:
                category = "ProgrammingError"; break;
            case SQLITE_NOMEM:
                PyErr_NoMemory();
                return;
        }
        const py::object type = py::module_::import("sqlite3").attr(category);
        py::object value = type(error.what());
        value.attr("sqlite_errorcode") = error.code();
        PyErr_SetObject(type.ptr(), value.ptr());
    }
}

} // namespace

namespace pybind11::detail {

// Keep coefficients arbitrary precision on both sides of the Python boundary.
// Hexadecimal avoids Python's decimal-string digit limit without modifying its
// global settings. SQLite and LiE serialization still use decimal integers.
// No C long, floating point, or implementation-dependent GMP limb conversion.
template <> struct type_caster<mpz_class> {
    PYBIND11_TYPE_CASTER(mpz_class, const_name("int"));
    bool load(handle source, bool) {
        if (!PyLong_Check(source.ptr()) || PyBool_Check(source.ptr())) return false;
        PyObject* text = PyNumber_ToBase(source.ptr(), 16);
        if (text == nullptr) throw py::error_already_set();
        const std::string hexadecimal = py::reinterpret_steal<py::str>(text).cast<std::string>();
        if (value.set_str(hexadecimal, 0) != 0) throw py::value_error("invalid integer coefficient");
        return true;
    }
    static handle cast(const mpz_class& source, return_value_policy, handle) {
        std::string hexadecimal = source.get_str(16);
        PyObject* result = PyLong_FromString(hexadecimal.data(), nullptr, 16);
        if (result == nullptr) throw py::error_already_set();
        return result;
    }
};

// Labels and Powers share this type. Immutable tuples preserve existing cache
// key semantics, and strict input conversion rejects float/bool coercion.
template <> struct type_caster<chars::Labels> {
    PYBIND11_TYPE_CASTER(chars::Labels, const_name("tuple[int, ...]"));
    bool load(handle source, bool) {
        if (PyUnicode_Check(source.ptr()) || PyBytes_Check(source.ptr())) return false;
        if (!PyObject_HasAttrString(source.ptr(), "__iter__")) return false;
        for (handle item : py::reinterpret_borrow<py::iterable>(source)) {
            value.push_back(integer64(item));
        }
        return true;
    }
    static handle cast(const chars::Labels& source, return_value_policy, handle) {
        py::tuple result(source.size());
        for (std::size_t i = 0; i < source.size(); ++i) result[i] = py::int_(source[i]);
        return result.release();
    }
};

template <> struct type_caster<chars::Request> {
    PYBIND11_TYPE_CASTER(chars::Request, const_name("tuple[str, tuple[int, ...], tuple[int, ...]]"));
    bool load(handle source, bool) {
        if (!PySequence_Check(source.ptr()) || PyUnicode_Check(source.ptr()) || PyBytes_Check(source.ptr())) return false;
        const auto tuple = py::reinterpret_borrow<py::sequence>(source);
        if (tuple.size() != 3) return false;
        value = {py::cast<std::string>(tuple[0]), py::cast<chars::Labels>(tuple[1]), py::cast<chars::Powers>(tuple[2])};
        return true;
    }
    static handle cast(const chars::Request& source, return_value_policy, handle) {
        return py::make_tuple(source.algebra, source.labels, source.powers).release();
    }
};

template <> struct type_caster<chars::Product> {
    PYBIND11_TYPE_CASTER(chars::Product, const_name("tuple[tuple[tuple[int, ...], tuple[int, ...]], ...]"));
    bool load(handle source, bool) {
        if (PyUnicode_Check(source.ptr()) || PyBytes_Check(source.ptr()) || !PyObject_HasAttrString(source.ptr(), "__iter__")) return false;
        for (handle item : py::reinterpret_borrow<py::iterable>(source)) {
            value.push_back(py::cast<std::pair<chars::Labels, chars::Powers>>(item));
        }
        return true;
    }
    static handle cast(const chars::Product& source, return_value_policy, handle) {
        py::tuple result(source.size());
        for (std::size_t i = 0; i < source.size(); ++i) result[i] = py::make_tuple(source[i].first, source[i].second);
        return result.release();
    }
};

template <> struct type_caster<std::vector<chars::Powers>> {
    PYBIND11_TYPE_CASTER(std::vector<chars::Powers>, const_name("tuple[tuple[int, ...], ...]"));
    bool load(handle source, bool) {
        if (PyUnicode_Check(source.ptr()) || PyBytes_Check(source.ptr()) || !PyObject_HasAttrString(source.ptr(), "__iter__")) return false;
        for (handle item : py::reinterpret_borrow<py::iterable>(source)) value.push_back(py::cast<chars::Powers>(item));
        return true;
    }
    static handle cast(const std::vector<chars::Powers>& source, return_value_policy, handle) {
        py::tuple result(source.size());
        for (std::size_t i = 0; i < source.size(); ++i) result[i] = py::cast(source[i]);
        return result.release();
    }
};

} // namespace pybind11::detail

PYBIND11_MODULE(_index_calculation_cpp, module) {
    module.doc() = "Native index expansion, singlet projection, and exact SQLite/LiE character cache; "
                   "cache instances are owned by one thread.";
    module.attr("SCHEMA_VERSION") = 1;
    py::register_local_exception_translator(&translate_sqlite_error);
    module.attr("SqliteError") = py::module_::import("sqlite3").attr("DatabaseError");

    using Release = py::call_guard<py::gil_scoped_release>;
    module.def("canonical_cartan_type", &chars::canonical_cartan_type, Release());
    module.def("canonical_labels", &chars::canonical_labels, Release());
    module.def("canonical_adams_powers", &chars::canonical_adams_powers, Release());
    module.def("adams_order", &chars::adams_order, Release());
    module.def("canonical_request", &chars::canonical_request, Release());
    module.def("canonical_product", &chars::canonical_product, Release());
    module.def("decomposition_dependencies", &chars::decomposition_dependencies, Release());
    module.def("dual_permutation", [](const std::string& algebra) {
        const auto permutation = released([&] { return chars::dual_permutation(algebra); });
        py::tuple result(permutation.size());
        for (std::size_t i = 0; i < permutation.size(); ++i) result[i] = permutation[i];
        return result;
    });
    module.def("singlet_pair", &chars::singlet_pair, Release());
    module.def("split_character_product", &chars::split_character_product, Release());
    module.def("format_labels", &chars::format_labels, Release());
    module.def("format_lie_decomposition", &chars::format_lie_decomposition, Release());
    module.def("parse_lie_decomposition", [](const std::string& text, py::handle rank) {
        const int value = small_integer(rank);
        return released([&] { return chars::parse_lie_decomposition(text, value); });
    });
    module.def("encode_decomposition", &chars::encode_decomposition, Release());
    module.def("decode_decomposition", &chars::decode_decomposition, Release());
    module.def("character_product_key", &chars::character_product_key, Release());
    module.def("decomposed_product_key", &chars::decomposed_product_key, Release());
    module.def("lie_output_lines", &chars::lie_output_lines, Release());

    // Keep the large formal expansion on the C++ side. Only the final sparse
    // Laurent coefficients cross Python, as arbitrary-precision Fractions.
    module.def("expand_and_project", [](py::handle order_input,
        const std::vector<std::string>& factors, py::iterable specs_input,
        py::iterable vectors_input, py::dict matter_input,
        chars::CharacterDecompositionCache& cache, py::handle workers_input, double timeout) {
        const int order = small_integer(order_input), workers = small_integer(workers_input);
        std::vector<n2scftdb::IndexCharacterSpec> specs;
        for (py::handle input : specs_input) {
            auto spec = py::cast<py::sequence>(input);
            if (spec.size() != 2) throw py::value_error("character spec must contain factor and labels");
            specs.push_back({small_integer(spec[0]), py::cast<chars::Labels>(spec[1])});
        }
        if (specs.size() > static_cast<std::size_t>(std::numeric_limits<int>::max()))
            throw py::value_error("too many formal characters");
        std::vector<int> vectors;
        for (py::handle input : vectors_input) vectors.push_back(small_integer(input));
        n2scftdb::MatterMultiplicities matter;
        for (auto input : matter_input) {
            if (!PyLong_Check(input.second.ptr()) || PyBool_Check(input.second.ptr()))
                throw py::type_error("matter multiplicity must be an integer");
            std::vector<int> indices;
            for (py::handle index : py::cast<py::iterable>(input.first))
                indices.push_back(small_integer(index));
            matter[std::move(indices)] = py::cast<mpz_class>(input.second);
        }
        const auto projected = released([&] {
            const auto terms = n2scftdb::expand_index_unordered(order, static_cast<int>(specs.size()),
                                                               vectors, matter, workers, timeout);
            return n2scftdb::project_index(terms, factors, specs, cache);
        });
        const auto fraction = py::module_::import("fractions").attr("Fraction");
        py::dict result;
        for (const auto& [powers, value] : projected) {
            const auto& [t, y, u] = powers;
            result[py::make_tuple(t, y, u)] = fraction(py::cast(mpz_class(value.get_num())),
                                                     py::cast(mpz_class(value.get_den())));
        }
        return result;
    }, py::arg("order"), py::arg("factors"), py::arg("character_specs"),
       py::arg("vector_characters"), py::arg("matter_multiplicities"), py::arg("cache"),
       py::arg("worker_count") = 1, py::arg("timeout") = 600.0);

    py::class_<chars::ProcessResult>(module, "ProcessResult")
        .def_readonly("returncode", &chars::ProcessResult::returncode)
        .def_readonly("stdout_text", &chars::ProcessResult::stdout_text)
        .def_readonly("stderr_text", &chars::ProcessResult::stderr_text)
        .def_property_readonly("stdout", [](const chars::ProcessResult& value) { return value.stdout_text; })
        .def_property_readonly("stderr", [](const chars::ProcessResult& value) { return value.stderr_text; });

    py::class_<chars::CharacterDecompositionCache>(module, "CharacterDecompositionCache")
        .def(py::init([](const std::string& path, const std::string& executable,
                        py::handle nodes, py::handle objects, double timeout, const std::string& backend) {
            const auto max_nodes = integer64(nodes), max_objects = integer64(objects);
            return released([&] {
                return std::make_unique<chars::CharacterDecompositionCache>(path, executable, max_nodes, max_objects, timeout, backend);
            });
        }), py::arg("database_path"), py::arg("lie_executable") = "lie",
            py::arg("max_nodes") = 9999999, py::arg("max_objects") = 9999999,
            py::arg("timeout") = 600.0, py::arg("lie_backend") = "auto")
        .def_property_readonly("lie_backend", &chars::CharacterDecompositionCache::lie_backend)
        .def("close", &chars::CharacterDecompositionCache::close, Release())
        .def("lookup_decomposition", &chars::CharacterDecompositionCache::lookup_decomposition, Release())
        .def("write_decompositions", &chars::CharacterDecompositionCache::write_decompositions, Release())
        .def("get_decomposition", &chars::CharacterDecompositionCache::get_decomposition, Release())
        .def("get_decompositions", &chars::CharacterDecompositionCache::get_decompositions, Release())
        .def("decomposition_dependencies", &chars::CharacterDecompositionCache::decomposition_dependencies, Release())
        .def("calculate_decomposition", &chars::CharacterDecompositionCache::calculate_decomposition, Release())
        .def("tensor_decompositions", &chars::CharacterDecompositionCache::tensor_decompositions, Release())
        .def("lookup_singlets", &chars::CharacterDecompositionCache::lookup_singlets, Release())
        .def("write_singlets", &chars::CharacterDecompositionCache::write_singlets, Release())
        .def("get_singlet_multiplicities", [](chars::CharacterDecompositionCache& self, const std::string& algebra,
                                            py::handle rank, const std::vector<chars::Product>& products) {
            const int value = small_integer(rank);
            return released([&] { return self.get_singlet_multiplicities(algebra, value, products); });
        })
        .def("singlet_multiplicities", [](chars::CharacterDecompositionCache& self, const std::string& algebra,
                                        py::handle rank, const std::vector<std::vector<chars::Decomposition>>& products) {
            const int value = small_integer(rank);
            return released([&] { return self.singlet_multiplicities(algebra, value, products); });
        })
        .def("character_singlets", [](chars::CharacterDecompositionCache& self, const std::string& algebra,
                                     py::handle rank, const std::vector<chars::Product>& products) {
            const int value = small_integer(rank);
            return released([&] { return self.character_singlets(algebra, value, products); });
        })
        .def("compute_singlet_multiplicities", [](chars::CharacterDecompositionCache& self, const std::string& algebra,
                                                py::handle rank, const std::vector<std::vector<chars::Decomposition>>& products) {
            const int value = small_integer(rank);
            return released([&] { return self.compute_singlet_multiplicities(algebra, value, products); });
        })
        .def("execute_lie", &chars::CharacterDecompositionCache::execute_lie,
             py::arg("expressions"), py::arg("configure_memory"), Release())
        .def("run_lie", &chars::CharacterDecompositionCache::run_lie, Release());
}

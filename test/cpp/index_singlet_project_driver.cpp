// JSON is only a test transport. The production API uses native C++ objects.
#include "index/cpp/index_singlet_project.hpp"
#include <nlohmann/json.hpp>
#include <iostream>
#include <stdexcept>

using Json = nlohmann::json;
using namespace n2scftdb;

int main(int argc, char** argv) {
    try {
        if (argc < 2) throw std::invalid_argument("expected cache path");
        Json input;
        std::cin >> input;
        const auto factors = input.at("factors").get<std::vector<std::string>>();
        std::vector<IndexCharacterSpec> specs;
        for (const auto& spec : input.at("characters"))
            specs.push_back({spec.at(0).get<int>(), spec.at(1).get<characters::Labels>()});
        std::vector<IndexFormTerm> terms;
        if (input.contains("expansion")) {
            const auto& expansion = input.at("expansion");
            MatterMultiplicities matter;
            for (const auto& item : expansion.at("matter"))
                matter[item.at(0).get<IndexedMonomial>()] += mpz_class(item.at(1).get<std::string>());
            const int order = expansion.at("order").get<int>();
            const auto vectors = expansion.at("vectors").get<std::vector<int>>();
            const int workers = expansion.value("workers", 1);
            terms = expansion.value("sorted", false)
                ? expand_index(order, static_cast<int>(specs.size()), vectors, matter, workers)
                : expand_index_unordered(order, static_cast<int>(specs.size()), vectors, matter, workers);
        } else {
            for (const auto& item : input.at("terms")) {
                IndexFormTerm term;
                term.coefficient = mpq_class(mpz_class(item.at("coefficient").at(0).get<std::string>()),
                                             mpz_class(item.at("coefficient").at(1).get<std::string>()));
                term.coefficient.canonicalize();
                term.t_power = item.at("powers").at(0).get<int>();
                term.y_power = item.at("powers").at(1).get<std::int64_t>();
                term.u_power = item.at("powers").at(2).get<std::int64_t>();
                for (const auto& part : item.at("characters"))
                    term.characters.push_back({part.at(0).get<int>(), part.at(1).get<characters::Powers>()});
                terms.push_back(std::move(term));
            }
        }
        ProjectedIndex projected;
        const std::string mode = argc > 2 ? argv[2] : "convenience";
        if (mode == "default") {
            projected = project_index(terms, factors, specs);
        } else if (mode == "borrowed" || mode == "cache-only") {
            // cache-only proves persistent hits without any available LiE engine.
            characters::CharacterDecompositionCache cache(argv[1],
                mode == "cache-only" ? "/no/such/lie" : "lie");
            projected = project_index(terms, factors, specs, cache);
            if (project_index(terms, factors, specs, cache) != projected)
                throw std::runtime_error("borrowed cache could not be reused");
        } else {
            projected = project_index(terms, factors, specs, std::string(argv[1]));
        }
        Json output = Json::array();
        for (const auto& [powers, coefficient] : projected) {
            const auto& [t, y, u] = powers;
            output.push_back({t, y, u, coefficient.get_num().get_str(), coefficient.get_den().get_str()});
        }
        std::cout << output.dump() << '\n';
        return 0;
    } catch (const std::invalid_argument& error) {
        std::cerr << "invalid_argument: " << error.what() << '\n';
    } catch (const std::overflow_error& error) {
        std::cerr << "overflow_error: " << error.what() << '\n';
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
    }
    return 1;
}

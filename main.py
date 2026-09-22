import sys


# 스크립트를 실행하려면 여백의 녹색 버튼을 누릅니다.
if __name__ == '__main__':
    print(sys.version)

    gauge_groups = ([f"A{i}" for i in range(1, 11)]
                    + [f"B{i}" for i in range(2, 11)]
                    + [f"C{i}" for i in range(2, 11)]
                    + [f"D{i}" for i in range(4, 11)]
                    + ["E6", "E7", "E8", "F4", "G2"])

    with open("theory_single.txt", "w") as f:
        for i in range(len(gauge_groups)):
            f.write(f"{gauge_groups[i]}\n")

    with open("theory_double.txt", "w") as f:
        for i in range(len(gauge_groups)):
            for j in range(i, len(gauge_groups)):
                f.write(f"{gauge_groups[i]}, {gauge_groups[j]}\n")

    with open("theory_triple.txt", "w") as f:
        for i in range(len(gauge_groups)):
            for j in range(i, len(gauge_groups)):
                for k in range(j, len(gauge_groups)):
                    f.write(f"{gauge_groups[i]}, {gauge_groups[j]}, {gauge_groups[k]}\n")

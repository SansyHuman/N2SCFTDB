import sys


# 스크립트를 실행하려면 여백의 녹색 버튼을 누릅니다.
if __name__ == '__main__':
    print(sys.version)
    with open("theory_single.txt", "w") as f:
        for i in range(1, 11):
            f.write(f"A{i}\n")
            f.write(f"B{i}\n")
            f.write(f"C{i}\n")
            f.write(f"D{i}\n")
        f.write(f"E6\n")
        f.write(f"E7\n")
        f.write(f"E8\n")
        f.write(f"F4\n")
        f.write(f"G2\n")

    with open("theory_double_a.txt", "w") as f:
        for i in range(1, 11):
            for j in range(i, 11):
                f.write(f"A{i}, A{j}\n")

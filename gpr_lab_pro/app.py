def main() -> None:
    try:
        from gpr_lab_pro.app.main import main as package_main

        package_main()
    except RuntimeError as exc:
        print(str(exc))


if __name__ == "__main__":
    main()

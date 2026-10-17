import argparse
import multiprocessing


def main():
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(description="拾信 Gmail 分類與摘要")
    parser.add_argument("--demo", action="store_true", help="直接使用合成信件 Demo")
    args = parser.parse_args()
    # CustomTkinter enables per-monitor Windows DPI awareness itself.
    from .ui import App
    app = App(demo=args.demo)
    app.mainloop()


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""生成 test_e2e.py 使用的占位测试图。"""
import os

from PIL import Image

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, 'test_images')
SUB = os.path.join(OUT, 'sub')


def main():
    os.makedirs(SUB, exist_ok=True)
    Image.new('RGB', (220, 220), (230, 120, 80)).save(os.path.join(OUT, 'input.jpg'), quality=90)
    Image.new('RGB', (800, 500), (80, 120, 200)).save(os.path.join(OUT, 'mid.jpg'), quality=90)
    Image.new('RGBA', (300, 200), (120, 200, 120, 160)).save(os.path.join(OUT, 'alpha_small.png'))
    Image.new('RGB', (256, 256), (160, 80, 160)).save(os.path.join(SUB, 'input2.jpg'), quality=90)
    print('测试图已生成:', OUT)


if __name__ == '__main__':
    main()

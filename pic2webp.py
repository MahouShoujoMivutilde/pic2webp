#! python3

from os import path, walk, remove
from multiprocessing import Pool, freeze_support
from time import clock
from datetime import timedelta
from sys import argv, exit
from codecs import open as copen
from functools import partial
from itertools import chain
from PIL import Image
import argparse
import re
import psutil


Image.MAX_IMAGE_PIXELS = 1000*10**6 # Для очень больших изображений - т.е. предупреждения о бомбе декомпрессии не будет вплоть до картинок в 1000МП  

Q = 90

default_formats = ['jpeg', 'png'] # Изображения каких форматов будут кодироваться в webp

remove_after = True # Удалять исходник после кодирования?

UF = 'unknown_format'

def get_args():
    parser = argparse.ArgumentParser(description = "Скрипт для конвертирования изображений в webp") 
    parser.add_argument("--input", "-i", help = "Путь к изображению, папке с изображениями или .utf8.txt-списку папок (в котором каждый новый путь с новой строки)")
    parser.add_argument("--to_decode", "-d", action = "store_true", help = "Конвертировать из webp в png/jpeg в зависимости от режима изображения (с прозрачностью/без)")
    parser.add_argument("-q", default = Q, type = int, help = "Качество webp [0; 100], больше - лучше сжатие (при сжатии без потерь), лучше качество (если с потерями); то же самое значение используется и при обратном конвертировании - в jpeg")
    parser.add_argument("-exif", action = "store_true", help = "Вывод exif webp-изображения по заданному пути")
    parser.add_argument("-L", action = "store_true", help = "Сжатие png без потерь")
    parser.add_argument("-f", default = default_formats, type = lambda s: s.split(','),  help = "Кастомный список кодируемых форматов (остальные игнорятся) для конвертации в webp, через запятую без пробелов - см. доступные через --supported, дефолтно '{}'".format(','.join(default_formats)))
    parser.add_argument("--supported", action = "store_true",  help = "Вывести список всех поддерживаемых форматов данной версии Pillow")
    return parser.parse_args()

def prepare_supported(supported):
    sup = [f.lower() for f in supported[:]]
    if 'jpg' in sup:
        sup.append('jpeg')
    return list(set(sup))

def show_supported_formats():
    def rebuild_dic(formats, exts):
        return {f:[ext for ext, fmt in exts.items() if f == fmt] for f in formats}

    exts = Image.registered_extensions()
    formats = sorted(list(set(exts.values())))

    for f, exts in rebuild_dic(formats, exts).items():
        print('{} ({})'.format(f, ', '.join(exts)))

def show_exif(fp):
    def get_exif(fp):
        from PIL.ExifTags import TAGS
        ret = {}
        with Image.open(fp) as i:
            info = i._getexif()
        if info:
            for tag, value in info.items():
                decoded = TAGS.get(tag, tag)
                if decoded != 'MakerNote': # Это тег без стандартных спецификаций, туда производитель может писать всё, что душе угодно, так что непредсказуем...
                    ret[decoded] = value
        else:
            ret = None
        return ret

    if path.isfile(fp) and get_type(fp) == 'webp':
        exif_dic = get_exif(fp)
        if exif_dic:
            for name in exif_dic:
                print('  {}: {}'.format(name, exif_dic.get(name)))
        else:
            print('нет exif метаданных')
    else:
        print('не webp / не существует')

def parse_txt(txt):
    L = []
    with copen(txt, 'r', 'utf-8') as f:
        for line in f.readlines():
            p = line.replace('\r\n', '')
            if path.isdir(p):
                L.append(p)
            else:
                print('{} - пропущен, т.к. не папка / не существует'.format(p))
    return L

def get_type(fp):
    try:
        with Image.open(fp) as img:
            return img.format.lower()
    except:
        return UF

def get_files(folder):
    for dirpath, dirnames, filenames in walk(folder):
        for f in filenames:
            yield path.join(dirpath, f)

def is_sup(fp, supported):
    if get_type(fp) in supported:
        return fp

def lower_child_priority():
    parent = psutil.Process()
    parent.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    for child in parent.children():
        child.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)

def sizeof_fmt(num, suffix='B'):  # http://stackoverflow.com/a/1094933
    for unit in ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi']:
        if abs(num) < 1024.0:
            return "%3.1f%s%s" % (num, unit, suffix)
        num /= 1024.0
    return "%.1f%s%s" % (num, 'Yi', suffix)

def encode(fp, lossless_png, quality, back_convert = False):
    def get_PIL_image(fp):
        with Image.open(fp) as image:
            if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
                return image.convert('RGBA')
            else:
                return image.convert('RGB')
    
    def get_webp_path(fp, lossless_png, quality):
        base = path.splitext(fp)[0]
        if get_type(fp) == 'png' and lossless_png:
            return '%s_qLL.webp' % base
        else:
            return '%s_q%d.webp' % (base, quality)
    
    def get_back_img_path(fp, image):
        name = path.basename(fp)
        dp = path.dirname(fp)
        no_ext_name = re.sub('(_q\d{1,2}0{1}?|_qLL)', '', path.splitext(name)[0]) # чтобы убрать суффикс _qЧИСЛО
        if image.mode == 'RGBA':
            return path.join(dp, no_ext_name + '.png')
        else:
            return path.join(dp, no_ext_name + '.jpg')
    
    def size_difference(after, before):
        percentage_difference = lambda out_val, in_val: round(100*(out_val/in_val - 1), 2)
        out = path.getsize(after)
        original = path.getsize(before)
        dif = percentage_difference(out, original)
        return dif, out, original

    def print_info(fp, dst, size_dif):
        def pn(name):
            return name[:57] + '...' if len(name) > 57 else name.ljust(60, '.')
        
        def pp(size_dif):
            return size_dif if size_dif <= 0 else '+' + str(size_dif)

        on = path.basename(fp)
        ne = path.splitext(dst)[1]
        print(' {} >>> {}, {}%'.format(pn(on), ne, pp(size_dif)))

    try: 
        img = get_PIL_image(fp)

        options = img.info.copy()

        if back_convert:
            dst = get_back_img_path(fp, img)
            if img.mode == 'RGB':
                options.update({'quality': quality})
        else:
            dst = get_webp_path(fp, lossless_png, quality)
            options.update({
                'method': 6, # https://pillow.readthedocs.io/en/5.1.x/handbook/image-file-formats.html?highlight=webp#webp, хотя между 0 и 6 разницы как-то не видно...
                'quality': quality
            })
            if get_type(fp) == 'png' and lossless_png:
                options.update({'lossless': True})
        
        img.save(dst, **options)
        size_dif, new, original = size_difference(dst, fp)
        print_info(fp, dst, size_dif)
        
        try:
            if remove_after:
                remove(fp)
        except Exception as e:
            print('не получилось удалить: {}, \n{}'.format(fp, e))
        
        return new, original
    except Exception as e:
        print('что-то умерло:\n {}\n {}'.format(fp, e))
        return 0, 0

def final_output(resuls):
    def pp(sum_dif):
        return sizeof_fmt(sum_dif) if sum_dif <= 0 else '+' + sizeof_fmt(sum_dif)
    
    sum_dif = sum(map(lambda L: L[0], results)) - sum(map(lambda L: L[1], results))
    print('\nвсего: {}, {} файл(ов)\n'.format(pp(sum_dif), len(results)))

if __name__ == '__main__':
    freeze_support()
    args = get_args()
    
    sup = prepare_supported(args.f)

    if args.supported:
        show_supported_formats()
        exit()

    if args.input is None:
        raise ValueError('аргумент -i осутствует!')

    if args.exif:
        show_exif(args.input)
        exit()
    
    if args.to_decode:
        sup = ['webp']

    files = []
    paths = []
    if path.isfile(args.input) and path.splitext(args.input)[1] == '.txt':
        paths = parse_txt(args.input)
    elif path.isfile(args.input) and get_type(args.input) in sup:
        files.append(path.abspath(args.input))
    elif path.isdir(args.input):
        paths.append(args.input)
    else:
        exit('жизнь меня к такому не готовила!')

    if paths:
        files.extend(chain(*(get_files(p) for p in paths)))

    start = clock()
    with Pool() as pool:
        lower_child_priority()
        sup_files = [file for file in pool.map(partial(is_sup, supported = sup), files) if file is not None]
        results = pool.map(partial(encode, lossless_png = args.L, quality = args.q, back_convert = args.to_decode), sup_files)
    
    if len(results) > 0:
        final_output(results)
    print('%s: %s\n%s' % (path.basename(argv[0]), str(timedelta(seconds = round(clock() - start))), '-'*34))
#! python3

from os import path, walk, remove, listdir, sep
from multiprocessing import Pool, freeze_support
from time import clock
from datetime import timedelta
from sys import argv, exit
from codecs import open as open2
from PIL import Image
import argparse
import re

Image.MAX_IMAGE_PIXELS = 1000*10**6 # Для очень больших изображений - т.е. предупреждения о бомбе декомпрессии не будет вплоть до картинок в 1000МП  

Q = 90

lossless_png = False # Сохраняет png, которые < 1920px по любой стороне без потерь, вообще.

sup = ['.jpg', '.jpeg', '.png'] # Изображения каких форматов будут кодироваться в webp

remove_after = True  # Удалять исходник после кодирования?

sup += [i.upper() for i in sup]


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

    if path.splitext(fp)[1] in ('.webp', '.WEBP') and path.isfile(fp):
        exif_dic = get_exif(fp)
        if exif_dic:
            for name in exif_dic:
                print('  {}: {}'.format(name, exif_dic.get(name)))
        else:
            print('нет exif метаданных')
    else:
        print('не webp / не существует')


def size_difference(after, before):
    def percentage_difference(out_val, in_val):
        return round(100*(out_val/in_val - 1), 2)

    out = path.getsize(after)
    original = path.getsize(before)
    dif = percentage_difference(out, original)
    return(dif, out, original)


def sizeof_fmt(num, suffix='B'):  # http://stackoverflow.com/a/1094933
    for unit in ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi']:
        if abs(num) < 1024.0:
            return "%3.1f%s%s" % (num, unit, suffix)
        num /= 1024.0
    return "%.1f%s%s" % (num, 'Yi', suffix)


def is_available(name):
    # на будущее
    # из-за многопроцессности нет возможности 100% надежно сделать это без остановок
    return name


def encode(fp):
    try:
        with Image.open(fp) as image:
            exif = image.info.get('exif', b'') # если exif есть - берет его, если нет - возвращает пустую строку байт
            image = image.convert('RGBA')
            small_size = image.width < 1920 and image.height < 1920
            base, ext = path.splitext(fp)
            name = path.split(fp)[1]
            if ext == '.png' and small_size and lossless_png:
                webp_path = is_available('%s_qLL.webp' % base)
                image.save(webp_path, lossless = True, exif = exif)
            else:
                webp_path = is_available('%s_q%d.webp' % (base, Q))
                image.save(webp_path, quality = Q, exif = exif)
            size_dif, webp, original = size_difference(webp_path, fp)
            print(' {} >>> .webp, {}%'.format(name[:57] + '...' if len(name) > 57 else name, size_dif if size_dif <= 0 else '+' + str(size_dif)))
        try:
            if remove_after:
                remove(fp)
        except Exception as e:
            print('не получилось удалить: {}, \n{}'.format(fp, e))
        return(webp, original)
    except Exception as e:
        print('что-то умерло: {}, \n{}'.format(fp, e))
        return(0, 0)


def decode(fp):
    try:
        with Image.open(fp) as image:
            base = path.splitext(fp)[0]
            name = path.split(fp)[1]
            no_ext = path.splitext(name)[0]
            base2 = sep.join(base.split(sep)[:-1])
            for patt in re.findall('(_q\d{1,2}0{1}?|_qLL)', no_ext): # чтобы убрать суффикс _qЧИСЛО
                no_ext = no_ext.replace(patt, '')
            if image.mode == 'RGBA': # прозрачность важнее метаданных
                image.save(path.join(base2, no_ext + '.png'))
            else:
                exif = image.info.get('exif', b'')
                image.save(path.join(base2, no_ext + '.jpg'), quality = 95, exif = exif)
            size_dif, out, original = size_difference(path.join(base2, no_ext + '.png') if image.mode == 'RGBA' else path.join(base2, no_ext + '.jpg'), fp)
            print(' {} >>> .{}'.format(name[:77] + '...' if len(name) > 77 else name, 'png' if image.mode == 'RGBA' else 'jpg'))
        try:
            remove(fp)
        except Exception as e:
            print('не получилось удалить: {}, \n{}'.format(fp, e))
        return(out, original)
    except Exception as e:
        print('что-то умерло: {}, \n{}'.format(fp, e))
        return(0, 0)


def get_args():
    parser = argparse.ArgumentParser(description = "Скрипт для конвертирования изображений в webp") 
    requiredNamed = parser.add_argument_group('required arguments')
    requiredNamed.add_argument("--input", "-i", required = True, help = "Путь к изображению, папке с изображениями или .utf8.txt-списку папок (в котором каждый новый путь с новой строки)")
    parser.add_argument("--to_decode", "-d", default = False, action = "store_true", help = "Конвертировать из webp в png/jpeg в зависимости от режима изображения (с прозрачностью/без)")
    parser.add_argument("-exif", default = False, action = "store_true", help = "Вывод exif webp-изображения по заданному пути")
    return parser.parse_args()


def get_files(Path, sup):  # возвращает только файлы, которые нужно кодировать.
    files = []
    for dirpath, dirnames, filenames in walk(Path):
        for f in filenames:
            fp = path.join(dirpath, f)
            ext = path.splitext(f)[1]
            if ext in sup:
                files.append(fp)
    return files

def parse_txt(txt):
    L = []
    with open2(txt, 'r', 'utf-8') as f:
        for line in f.readlines():
            p = line.replace('\r\n', '')
            if path.isdir(p):
                L.append(p)
            else:
                print('{} - пропущен, т.к. не папка / не существует'.format(p))
    return L

def final_output(resuls):
    sum_dif = sum(map(lambda L: L[0], results)) - sum(map(lambda L: L[1], results))
    print('\nвсего: {}, {} файл(ов)\n'.format(sizeof_fmt(sum_dif) if sum_dif <= 0 else '+' + sizeof_fmt(sum_dif), len(results)))

if __name__ == '__main__':
    freeze_support()
    args = get_args()
    if args.exif:
        show_exif(args.input)
        exit()

    if args.to_decode:
        sup = ['.webp', '.WEBP']

    files = []
    paths = []
    if path.isfile(args.input) and path.splitext(args.input)[1] == '.txt':
        paths = parse_txt(args.input)
    elif path.isfile(args.input) and path.splitext(args.input)[1] in sup:
        files.append(path.abspath(args.input))
    elif path.isdir(args.input):
        paths.append(args.input)
    else:
        exit('жизнь меня к такому не готовила!')

    for p in paths: # игнорится при paths = []
        files.extend(get_files(p, sup))

    start = clock()
    with Pool() as pool:
        try:
            import psutil
            parent = psutil.Process()
            parent.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            for child in parent.children():
                child.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        except:
            pass
        results = pool.map(encode if not args.to_decode else decode, files)
    if len(results) > 0:
        final_output(results)
    print('%s: %s\n%s' % (path.split(argv[0])[1], str(timedelta(seconds = round(clock() - start))), '-'*34))
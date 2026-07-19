# Adeept PiCar-Pro V1 firmware by jsck37

---

## 📋 Требования

- Raspberry Pi (3B/3B+/4/5 или совместимая модель)
- Карта microSD (от 8 ГБ)
- Подключение к интернету
- ОС: **Raspberry Pi OS** (64-bit или 32-bit)

---

## ⚙️ Установка

### 1. Подготовка образа ОС
1. Скачать [Raspberry Pi Imager](https://downloads.raspberrypi.com/imager/imager_latest.exe)
2. Выбрать ОС: [Raspberry Pi OS](https://www.raspberrypi.com/software/operating-systems/)
3. Записать образ на microSD и запустить Raspberry Pi

### 2. Подготовка Raspberry Pi

- microSD с установленной OS
- Адаптер питания
- HDMI-кабель для вывода изображения
- Периферия (мышка/клава)

### 3. Установка ПО

Открыть терминал в Raspberry Pi через SSH или напрямую:

```bash
# Обновление системы
sudo apt update -y && sudo apt upgrade -y

# Установка Git
sudo apt install git -y

# Клонирование репозитория
git clone https://github.com/jsck37/adeept_picarpro_v1

# Запуск установки
sudo python3 adeept_picarpro_v1/setup.py
```

### 4. Проверка службы автозагрузки

```bash
systemctl status picarpro

# Должно вывести loaded: enabled; active: active.
# Иначе пересоздать службу автозагрузки
```

### 5. Открытие веб-панели

Подключитесь к точке доступа "Adeept/0123456789"

Открыть одну из ссылок:
1. http://localhost:5000
2. http://10.42.0.1:5000

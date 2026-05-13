# Adeept PiCar-Pro V1

---

## 📋 Требования

- Raspberry Pi (3B/3B+/4/5 или совместимая модель)
- Карта microSD (от 8 ГБ)
- Подключение к интернету
- ОС: **Raspberry Pi OS** (64-bit или 32-bit)

---

## ⚙️ Установка

### 1. Подготовка образа ОС
1. Скачайте [Raspberry Pi Imager](https://downloads.raspberrypi.com/imager/imager_latest.exe)
2. Выберите ОС: [Raspberry Pi OS](https://www.raspberrypi.com/software/operating-systems/)
3. Запишите образ на microSD и запустите Raspberry Pi

### 2. Установка ПО

Подключитесь к Raspberry Pi через SSH или терминал и выполните:

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

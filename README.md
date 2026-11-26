# Zasięg zajezdni

https://436.pl/zasieg-zajezdni/ (kopia `site/` w repozytorium [mccartney/436.pl](https://github.com/mccartney/436.pl), `site/zasieg-zajezdni/`)

Jednorazowa wizualizacja: jak daleko sięgają zakłady obsługujące autobusy WTP i gdzie kończy się
„teren” jednego, a zaczyna drugiego. Dzień odniesienia: wtorek 1 grudnia 2026, linie dzienne
(bez N, L, Z), bez kursów zjazdowych i wyjazdowych (TD/TZ).

- Na każdym odcinku ulicy osobna wstęga dla każdego zakładu, grubość ~ √(kursy na dobę).
  Linie obsługiwane przez kilka zakładów rozkładają się na kilka wstęg.
- Kliknięcie zakładu w legendzie pokazuje tylko jego sieć i jego statystyki.

Kolory jak w [Przydziale brygad](https://436.pl/przydzial-brygad/), przyciemnione na potrzeby mapy.
Zajezdnie przewoźnika (Mobilis, Relobus) liczone razem.

## Budowanie

    curl -o feed/warsaw.zip https://436.pl/gtfs/warsaw.zip
    python3 build.py          # -> site/data.js

`site/` to cała strona (`index.html` + `data.js`); otwiera się wprost z dysku. MapLibre GL z unpkg,
podkład OpenFreeMap (dane © OpenStreetMap). Data odniesienia to `DATE` w `build.py` — musi mieścić
się w horyzoncie feedu (ok. tygodnia naprzód).

## Źródła danych

- [Zarząd Transportu Miejskiego w Warszawie](https://ztm.waw.pl)
- [GTFS: mccartney/WarsawGTFS](https://github.com/mccartney/WarsawGTFS) — `https://436.pl/gtfs/warsaw.zip`

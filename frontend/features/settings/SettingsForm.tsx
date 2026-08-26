"use client";

import useSWR from "swr";

import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Field, Input, Select, Switch } from "@/components/ui/Field";
import { Skeleton } from "@/components/ui/Stat";
import { useSettings } from "@/hooks/useSettings";
import { settingsService } from "@/services/user";
import { useUiStore } from "@/stores/uiStore";
import { formatPercent } from "@/utils/format";
import type { InflationSource, RiskProfile, UiMode, UserSettings } from "@/types/api";

export function SettingsForm() {
  const { settings, isLoading, update } = useSettings();
  const setUiMode = useUiStore((s) => s.setUiMode);
  const { data: inflation } = useSWR("inflation", () => settingsService.inflation(), {
    revalidateOnFocus: false,
  });
  const { data: dcfHealth } = useSWR("dcf-health", () => settingsService.dcfHealth(), {
    revalidateOnFocus: false,
  });
  const { data: monitoringHealth } = useSWR("monitoring-health", () => settingsService.monitoringHealth(), {
    revalidateOnFocus: false,
  });

  if (isLoading || !settings) return <Skeleton className="h-64 w-full" />;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Аккаунт" subtitle="Все функции сервиса доступны бесплатно и без ограничений." />
        <CardBody className="grid gap-3 sm:grid-cols-3">
          <div><p className="text-xs text-slate-500">Доступ</p><p className="mt-1 font-semibold">Бесплатный</p></div>
          <div><p className="text-xs text-slate-500">DCF-расчёты</p><p className="mt-1 font-semibold">Без лимита</p></div>
          <div><p className="text-xs text-slate-500">Подписка</p><p className="mt-1 font-semibold">Не требуется</p></div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Инфляция и реальная доходность"
          subtitle="Реальная доходность считается по формуле Фишера: (1+доходность)/(1+инфляция)−1"
        />
        <CardBody className="space-y-2">
          <Switch
            label="Учитывать инфляцию"
            description="Показывать, что останется от дохода после роста цен"
            checked={settings.inflation_enabled}
            onChange={(value) => void update({ inflation_enabled: value })}
          />
          <Switch
            label="Показывать «после инфляции» на карточках"
            checked={settings.show_real_return}
            onChange={(value) => void update({ show_real_return: value })}
          />

          <Field
            label="Источник инфляции"
            hint={
              inflation?.rate === null || inflation?.rate === undefined
                ? "Сейчас данных по инфляции нет — реальная доходность не рассчитывается."
                : `Сейчас используется ${formatPercent((inflation.rate ?? 0) * 100)} (${inflation.kind ?? "—"}, источник: ${inflation.source ?? "—"}).`
            }
          >
            <Select
              value={settings.inflation_source}
              onChange={(e) =>
                void update({ inflation_source: e.target.value as InflationSource })
              }
            >
              <option value="automatic">Автоматически (прогноз под срок вложения)</option>
              <option value="official">Официальная статистика</option>
              <option value="forecast">Прогноз</option>
              <option value="manual">Вручную</option>
            </Select>
          </Field>

          {settings.inflation_source === "manual" ? (
            <Field label="Своя инфляция, % в год" hint="Например, 9.5">
              <Input
                type="number"
                step="0.1"
                defaultValue={
                  settings.manual_inflation_rate === null
                    ? ""
                    : settings.manual_inflation_rate * 100
                }
                onBlur={(e) => {
                  const value = Number(e.target.value);
                  if (!Number.isNaN(value)) {
                    void update({ manual_inflation_rate: value / 100 });
                  }
                }}
              />
            </Field>
          ) : null}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Интерфейс" />
        <CardBody className="space-y-3">
          <Field
            label="Режим по умолчанию"
            hint="«Просто» показывает семь понятных показателей, «Подробно» добавляет YTM, duration и прочее."
          >
            <Select
              value={settings.ui_mode}
              onChange={(e) => {
                const mode = e.target.value as UiMode;
                setUiMode(mode);
                void update({ ui_mode: mode });
              }}
            >
              <option value="simple">Просто</option>
              <option value="pro">Подробно</option>
            </Select>
          </Field>

          <Field
            label="Профиль риска"
            hint="Влияет только на веса в общей оценке. Отдельные показатели остаются объективными."
          >
            <Select
              value={settings.risk_profile}
              onChange={(e) =>
                void update({ risk_profile: e.target.value as RiskProfile })
              }
            >
              <option value="conservative">Осторожный</option>
              <option value="balanced">Сбалансированный</option>
              <option value="aggressive">Агрессивный</option>
            </Select>
          </Field>

          <Field label="Тема">
            <Select
              value={settings.theme}
              onChange={(e) =>
                void update({ theme: e.target.value as "light" | "dark" | "system" })
              }
            >
              <option value="system">Как в системе</option>
              <option value="light">Светлая</option>
              <option value="dark">Темная</option>
            </Select>
          </Field>

          <Field label="Базовая валюта">
            <Select
              value={settings.base_currency}
              onChange={(e) => void update({ base_currency: e.target.value })}
            >
              <option value="KZT">KZT · тенге</option>
              <option value="USD">USD · доллар</option>
              <option value="EUR">EUR · евро</option>
              <option value="RUB">RUB · рубль</option>
            </Select>
          </Field>

          <Field label="Язык">
            <Select
              value={settings.language}
              onChange={(e) => void update({ language: e.target.value })}
            >
              <option value="ru">Русский</option>
              <option value="kk">Қазақша</option>
              <option value="en">English</option>
            </Select>
          </Field>

          <Switch
            label="Запоминать сумму в калькуляторе"
            checked={settings.remember_calculator_amount}
            onChange={(value) => void update({ remember_calculator_amount: value })}
          />
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="DCF"
          subtitle="Управляет подачей результата. Допущения и расчёт модели клиенту не раскрываются."
        />
        <CardBody className="space-y-2">
          <Switch
            label="Показывать уверенность анализа"
            checked={settings.show_dcf_confidence}
            onChange={(value) => void update({ show_dcf_confidence: value })}
          />
          <Switch
            label="Показывать разницу сценариев с рынком"
            checked={settings.show_dcf_scenario_differences}
            onChange={(value) => void update({ show_dcf_scenario_differences: value })}
          />
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Данные и анализ"
          subtitle="Настройки меняют представление и рекомендации, но не переписывают объективные финансовые показатели."
        />
        <CardBody className="space-y-2">
          <Switch
            label="Консервативный режим при неполных данных"
            description="Не делать оптимистичных выводов, если источников или истории недостаточно"
            checked={settings.conservative_missing_data_mode}
            onChange={(value) => void update({ conservative_missing_data_mode: value })}
          />
          <Switch
            label="Новости"
            checked={settings.news_enabled}
            onChange={(value) => void update({ news_enabled: value })}
          />
          <Switch
            label="Новости KASE"
            checked={settings.kase_news_enabled}
            onChange={(value) => void update({ kase_news_enabled: value })}
          />
          <Switch
            label="Внешние новости"
            checked={settings.external_news_enabled}
            onChange={(value) => void update({ external_news_enabled: value })}
          />
          <Switch
            label="Маркеры событий на графике"
            checked={settings.chart_news_markers_enabled}
            onChange={(value) => void update({ chart_news_markers_enabled: value })}
          />
          <Switch
            label="Прогноз модели"
            checked={settings.forecast_enabled}
            onChange={(value) => void update({ forecast_enabled: value })}
          />
          <Switch
            label="Интервалы неопределённости"
            checked={settings.uncertainty_intervals_enabled}
            onChange={(value) => void update({ uncertainty_intervals_enabled: value })}
          />
          <Field label="Диапазон графика по умолчанию">
            <Select
              value={settings.default_chart_range}
              onChange={(e) => void update({ default_chart_range: e.target.value as UserSettings["default_chart_range"] })}
            >
              <option value="1d">1 день</option><option value="5d">5 дней</option>
              <option value="1m">1 месяц</option><option value="3m">3 месяца</option>
              <option value="6m">6 месяцев</option><option value="1y">1 год</option>
              <option value="2y">2 года</option><option value="3y">3 года</option>
              <option value="5y">5 лет</option><option value="max">Вся история</option>
            </Select>
          </Field>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Расширенная диагностика" subtitle="Состояние сохранённых данных и моделей, а не обещание доступности внешнего сайта." />
        <CardBody className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <div><p className="text-xs text-slate-500">DCF модель</p><p className="mt-1 font-semibold">{dcfHealth?.engine.version ?? "—"}</p></div>
          <div><p className="text-xs text-slate-500">Финансовые отчёты</p><p className="mt-1 font-semibold">{dcfHealth?.financial_data.statements ?? "—"}</p></div>
          <div><p className="text-xs text-slate-500">Макро-данные</p><p className="mt-1 font-semibold">{dcfHealth?.macro_provider.status ?? "—"}</p></div>
          <div><p className="text-xs text-slate-500">Мониторинг / parser</p><p className="mt-1 font-semibold">{monitoringHealth?.status ?? monitoringHealth?.state ?? "—"}</p></div>
        </CardBody>
      </Card>

      <Card>
        <CardBody>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Настройки сохраняются для этого браузера без регистрации. Регистрация
            нужна только для синхронизации между устройствами, алертов и общего
            доступа к портфелю.
          </p>
        </CardBody>
      </Card>
    </div>
  );
}

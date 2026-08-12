"use client";

import useSWR from "swr";

import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Field, Input, Select, Switch } from "@/components/ui/Field";
import { Skeleton } from "@/components/ui/Stat";
import { useSettings } from "@/hooks/useSettings";
import { settingsService } from "@/services/user";
import { useUiStore } from "@/stores/uiStore";
import { formatPercent } from "@/utils/format";
import type { InflationSource, RiskProfile, UiMode } from "@/types/api";

export function SettingsForm() {
  const { settings, isLoading, update } = useSettings();
  const setUiMode = useUiStore((s) => s.setUiMode);
  const { data: inflation } = useSWR("inflation", () => settingsService.inflation(), {
    revalidateOnFocus: false,
  });

  if (isLoading || !settings) return <Skeleton className="h-64 w-full" />;

  return (
    <div className="space-y-4">
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

          <Switch
            label="Запоминать сумму в калькуляторе"
            checked={settings.remember_calculator_amount}
            onChange={(value) => void update({ remember_calculator_amount: value })}
          />
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

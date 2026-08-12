import { SettingsForm } from "@/features/settings/SettingsForm";

export const metadata = { title: "Настройки · KASE Bond AI" };

export default function SettingsPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold tracking-tight">Настройки</h1>
      <SettingsForm />
    </div>
  );
}

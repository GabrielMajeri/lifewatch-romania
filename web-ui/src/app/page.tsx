import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function HomePage() {
  return (
    <main className="min-h-screen flex flex-row justify-center items-center">
      <Card className="max-w-sm">
        <CardHeader>
          <CardTitle>Project Overview</CardTitle>
          <CardDescription>
            Track progress and recent activity for your Next.js app.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <p>
            Your design system is ready. Start building your next component.
          </p>
          <p className="mt-3">
            To get started, edit the <code>page.tsx</code> file.
          </p>
        </CardContent>
      </Card>
    </main>
  );
}

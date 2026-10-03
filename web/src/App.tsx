import { BrowserRouter, Route, Routes } from "react-router-dom";
import { LangProvider } from "./i18n/LangContext";
import { Layout } from "./components/Layout";
import { ArticleDetail } from "./pages/ArticleDetail";
import { Browse } from "./pages/Browse";
import { Home } from "./pages/Home";
import { NotFound } from "./pages/NotFound";

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Home />} />
        <Route path="/articles" element={<Browse />} />
        <Route path="/articles/:id" element={<ArticleDetail />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

export function App() {
  return (
    <LangProvider>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </LangProvider>
  );
}

-- Planificateur : fait avancer les audits chaque minute en appelant l'API Vercel.
-- À exécuter UNE fois dans Supabase → SQL Editor, après le premier déploiement Vercel,
-- en remplaçant VOTRE-DOMAINE et VOTRE_CRON_SECRET (même valeur que la variable CRON_SECRET de Vercel).

create extension if not exists pg_cron;
create extension if not exists pg_net;

-- Le secret est rangé dans le coffre de Supabase plutôt qu'en clair dans la tâche.
select vault.create_secret('VOTRE_CRON_SECRET', 'vigie_cron_secret');

select cron.schedule('vigie-tick', '* * * * *', $$
  select net.http_post(
    url := 'https://VOTRE-DOMAINE/api/worker/tick',
    headers := jsonb_build_object('Authorization', 'Bearer ' ||
      (select decrypted_secret from vault.decrypted_secrets where name = 'vigie_cron_secret')),
    timeout_milliseconds := 55000
  );
$$);

-- Vérifier : select * from cron.job_run_details order by start_time desc limit 5;
-- Arrêter : select cron.unschedule('vigie-tick');

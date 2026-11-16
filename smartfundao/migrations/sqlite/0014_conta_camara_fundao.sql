-- A conta camaramunicipaldofundao@townlyup.pt tinha sido criada como munícipe; passa a ser uma conta da
-- Câmara Municipal do Fundão (técnico da Câmara: vê todas as ocorrências do concelho e resolve-as).
UPDATE users
   SET role = 'staff',
       organization_id = (SELECT id FROM organizations WHERE kind = 'camara' ORDER BY id LIMIT 1),
       parish_code = NULL
 WHERE email = 'camaramunicipaldofundao@townlyup.pt' AND role = 'citizen'
   AND EXISTS (SELECT 1 FROM organizations WHERE kind = 'camara');
DELETE FROM user_parishes WHERE user_id IN (SELECT id FROM users WHERE email = 'camaramunicipaldofundao@townlyup.pt' AND role = 'staff');
